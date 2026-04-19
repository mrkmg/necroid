<#
.SYNOPSIS
    Shared helpers for mod.ps1 (and transitionally mods.ps1). Dot-source, don't execute.

.DESCRIPTION
    Pure-ish functions for path expansion, hashing, git invocation, config/state I/O,
    and robocopy. No script-scope dependencies except what callers pass in.
#>

Set-StrictMode -Version 3.0

function Expand-ConfigPath {
    param([string]$Raw, [string]$RootDir)
    if (-not $Raw) { return $Raw }
    $p = [System.Environment]::ExpandEnvironmentVariables($Raw)
    $p = [regex]::Replace($p, '\$\{([A-Za-z_][A-Za-z0-9_]*)\}', {
        param($m)
        $v = [System.Environment]::GetEnvironmentVariable($m.Groups[1].Value)
        if ($null -eq $v) { '' } else { $v }
    })
    if ($p.StartsWith('~')) { $p = Join-Path $HOME $p.Substring(1).TrimStart('/','\') }
    if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $RootDir $p }
    return [System.IO.Path]::GetFullPath($p)
}

function Get-FileHash256 {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-StringHash256 {
    param([string]$Text)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($bytes)
        return ($hash | ForEach-Object { $_.ToString('x2') }) -join ''
    } finally { $sha.Dispose() }
}

function Test-GitAvailable {
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "git.exe not found on PATH. Install Git for Windows:`n    winget install --id Git.Git -e"
    }
    return $cmd.Source
}

function Read-ModConfig {
    param([string]$RootDir)
    $configPath = Join-Path $RootDir '.mod-config.json'
    $legacyPath = Join-Path $RootDir 'mods.json'
    $raw = $null
    if (Test-Path -LiteralPath $configPath) {
        $raw = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } elseif (Test-Path -LiteralPath $legacyPath) {
        $raw = Get-Content -LiteralPath $legacyPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        throw "no config found. Run './mod.ps1 init' first, or create .mod-config.json."
    }
    $pzInstall = Expand-ConfigPath $raw.pzInstallDir $RootDir
    if (-not $pzInstall) { throw "config missing 'pzInstallDir'" }
    $originalsRaw = if ($raw.PSObject.Properties['originalsDir']) { $raw.originalsDir } else { 'classes-original' }
    $originals = Expand-ConfigPath $originalsRaw $RootDir
    return [pscustomobject]@{
        PzInstallDir = $pzInstall
        OriginalsDir = $originals
        RootDir      = $RootDir
        BuildDir     = Join-Path $RootDir 'build\classes'
        PristineDir  = Join-Path $RootDir 'src-pristine'
        SrcDir       = Join-Path $RootDir 'src'
        ModsDir      = Join-Path $RootDir 'mods'
        StateFile    = Join-Path $RootDir '.mod-state.json'
        EnterFile    = Join-Path $RootDir '.mod-enter.json'
        StageDir     = Join-Path $RootDir 'build\stage-src'
    }
}

function Write-ModConfig {
    param([string]$RootDir, [string]$PzInstallDir, [string]$OriginalsDir = 'classes-original')
    $path = Join-Path $RootDir '.mod-config.json'
    $obj = [ordered]@{
        pzInstallDir = $PzInstallDir
        originalsDir = $OriginalsDir
    }
    ($obj | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $path -Encoding UTF8
}

function Read-ModState {
    param([string]$StateFile)
    if (-not (Test-Path -LiteralPath $StateFile)) {
        return [pscustomobject]@{ version = 1; stack = @(); installedAt = $null; installed = @() }
    }
    return Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-ModState {
    param([string]$StateFile, [object]$State)
    ($State | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Read-ModJson {
    param([string]$ModDir)
    $path = Join-Path $ModDir 'mod.json'
    if (-not (Test-Path -LiteralPath $path)) { throw "mod.json not found in $ModDir" }
    return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-ModJson {
    param([string]$ModDir, [object]$ModObj)
    $path = Join-Path $ModDir 'mod.json'
    ($ModObj | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $path -Encoding UTF8
}

# List patches in a mod as objects with: Rel (e.g. zombie/Lua/Event.java),
# Kind (patch|new|delete), File (absolute path to patch/new/delete file).
function Get-ModPatchItems {
    param([string]$ModDir)
    $patchesDir = Join-Path $ModDir 'patches'
    if (-not (Test-Path -LiteralPath $patchesDir)) { return @() }
    $items = @()
    Get-ChildItem -LiteralPath $patchesDir -Recurse -File | ForEach-Object {
        $relFull = [System.IO.Path]::GetRelativePath($patchesDir, $_.FullName) -replace '\\','/'
        $kind = $null
        $rel  = $null
        if ($relFull.EndsWith('.java.patch'))     { $kind = 'patch';  $rel = $relFull.Substring(0, $relFull.Length - '.patch'.Length) }
        elseif ($relFull.EndsWith('.java.new'))   { $kind = 'new';    $rel = $relFull.Substring(0, $relFull.Length - '.new'.Length) }
        elseif ($relFull.EndsWith('.java.delete')){ $kind = 'delete'; $rel = $relFull.Substring(0, $relFull.Length - '.delete'.Length) }
        if ($kind) {
            $items += [pscustomobject]@{
                Rel  = $rel
                Kind = $kind
                File = $_.FullName
            }
        }
    }
    return $items
}

# Compute per-mod pristine snapshot: concat SHA256s of each file this mod touches
# from pristine, and hash the result. Stable across reruns.
function Get-PristineSnapshotForMod {
    param([string]$PristineDir, [object[]]$Items)
    $parts = @()
    foreach ($it in ($Items | Sort-Object Rel)) {
        $p = Join-Path $PristineDir $it.Rel
        $h = Get-FileHash256 $p
        if (-not $h) { $h = 'ABSENT' }
        $parts += "$($it.Rel)|$h"
    }
    return Get-StringHash256 ($parts -join "`n")
}

function Copy-Tree-Mirror {
    param([string]$Src, [string]$Dst)
    if (-not (Test-Path -LiteralPath $Src)) { throw "source missing: $Src" }
    if (-not (Test-Path -LiteralPath $Dst)) { New-Item -ItemType Directory -Force -Path $Dst | Out-Null }
    $null = & robocopy $Src $Dst /MIR /NFL /NDL /NJH /NJS /NC /NS /NP
    # robocopy exit codes 0-7 are success (8+ is error).
    if ($LASTEXITCODE -ge 8) { throw "robocopy $Src -> $Dst failed (exit $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

function New-EmptyDir {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) { Remove-Item -Recurse -Force -LiteralPath $Path }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

# Generate a unified diff between pristine file and working file.
# Writes to $OutPath. Rewrites a/ and b/ headers so patches are portable
# (relative to stage root "zombie/...").
function Write-UnifiedDiff {
    param(
        [string]$PristineFile,
        [string]$WorkingFile,
        [string]$RelPath,     # e.g. zombie/Lua/Event.java
        [string]$OutPath
    )
    $gitArgs = @(
        '-c', 'core.autocrlf=false',
        '-c', 'core.safecrlf=false',
        'diff', '--no-index', '--no-color', '--no-renames', '-U3', '--',
        $PristineFile, $WorkingFile
    )
    $raw = & git.exe @gitArgs
    # git diff --no-index returns 1 when files differ (that's fine), 0 when identical, 2+ on error.
    if ($LASTEXITCODE -ge 2) { throw "git diff failed for $RelPath (exit $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
    if (-not $raw) { return $false }  # identical

    # Rewrite headers to forward-slash relative form.
    $aHeader = "a/$RelPath"
    $bHeader = "b/$RelPath"
    $lines = @()
    foreach ($line in $raw) {
        if     ($line -like 'diff --git *')      { $lines += "diff --git $aHeader $bHeader" }
        elseif ($line -like '--- *')             { $lines += "--- $aHeader" }
        elseif ($line -like '+++ *')             { $lines += "+++ $bHeader" }
        else                                     { $lines += $line }
    }
    $text = ($lines -join "`n") + "`n"
    $parent = Split-Path $OutPath -Parent
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    [System.IO.File]::WriteAllText($OutPath, $text, [System.Text.UTF8Encoding]::new($false))
    return $true
}

# Apply a unified patch to a file. Returns $true on success, $false on failure
# (caller decides whether to fall back to 3-way merge). git apply rejects absolute
# Windows paths in --directory (treats "C:" as part of the path), so we cd to
# $WorkDir and apply without --directory; patch headers like a/zombie/Lua/Event.java
# resolve relative to CWD.
#
# When $WorkDir lives inside a parent git repo (e.g. ./build/stage-src under a
# repo-rooted PZ-Mod-Work), `git apply` will silently skip the patch because the
# `diff --git a/.. b/..` + `index ...` header makes it consult the repo index for
# files that don't exist there. Workaround: strip those metadata lines so git
# falls back to plain `--- file.orig` / `+++ file.new` resolution against CWD.
function Invoke-GitApplyFile {
    param(
        [string]$PatchFile,
        [string]$WorkDir,
        [string]$RelPath
    )
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        $stripped = (Get-Content -LiteralPath $PatchFile) | Where-Object {
            $_ -notmatch '^diff --git ' -and $_ -notmatch '^index [0-9a-f]+\.\.[0-9a-f]+'
        }
        # WriteAllLines uses Environment.NewLine (\r\n) which corrupts LF patches
        # against LF source. Join with LF explicitly.
        [System.IO.File]::WriteAllText($tmp, (($stripped -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
        Push-Location -LiteralPath $WorkDir
        try {
            $null = & git.exe -c 'core.autocrlf=false' apply --whitespace=nowarn -- $tmp 2>&1
            $code = $LASTEXITCODE
            $global:LASTEXITCODE = 0
            return ($code -eq 0)
        } finally { Pop-Location }
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

# 3-way merge of <current> with <incoming> against <base>. Modifies $Current in place.
# Returns $true on clean merge, $false on conflict (conflict markers written to $Current).
function Invoke-GitMergeFile {
    param(
        [string]$Current,
        [string]$Base,
        [string]$Incoming
    )
    $null = & git.exe merge-file -L current -L base -L incoming $Current $Base $Incoming
    $code = $LASTEXITCODE
    $global:LASTEXITCODE = 0
    # Exit 0 = clean; >0 = number of conflicts; <0 = error.
    return ($code -eq 0)
}

# Produce a new "theirs" file by applying a single patch to a pristine copy
# at $ScratchDir (fresh tree). Returns the path to the patched file, or $null
# if apply failed.
function New-PatchedTheirsFile {
    param(
        [string]$PristineDir,
        [string]$ScratchDir,
        [string]$PatchFile,
        [string]$RelPath
    )
    $src = Join-Path $PristineDir $RelPath
    if (-not (Test-Path -LiteralPath $src)) { return $null }
    $dst = Join-Path $ScratchDir $RelPath
    $parent = Split-Path $dst -Parent
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    # Apply patch with --directory=$ScratchDir so a/<RelPath> resolves to $dst.
    if (Invoke-GitApplyFile -PatchFile $PatchFile -WorkDir $ScratchDir -RelPath $RelPath) {
        return $dst
    }
    return $null
}
