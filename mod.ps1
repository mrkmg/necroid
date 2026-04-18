<#
.SYNOPSIS
    Diff-based mod manager for PZ-Mod-Work. Each mod is a directory of unified
    diffs against a frozen decompile of the pristine source (src-pristine/).

.DESCRIPTION
    Replaces mods.ps1 / mods.json. Workflow:

      ./mod.ps1 init                                  # one-time: populate src-pristine/ via Vineflower
      ./mod.ps1 new my-mod -Description "..."         # create mods/my-mod/
      ./mod.ps1 enter my-mod                          # reset src/, apply my-mod's patches, start editing
      # ...edit files in src/zombie/...
      ./mod.ps1 capture my-mod                        # diff src/ vs src-pristine/ into my-mod's patches
      ./mod.ps1 install my-mod                        # stage+compile+install .class files to PZ
      ./mod.ps1 uninstall                             # restore everything install wrote

    Multiple mods stack:
      ./mod.ps1 enter mod-a mod-b                     # apply both in order
      ./mod.ps1 install mod-a mod-b                   # compile+install union of changes

    Requires git.exe on PATH (Git for Windows).

.PARAMETER Command
    init | new | list | status | enter | capture | diff | reset | install | uninstall | verify | resync-pristine | help

.PARAMETER Rest
    Positional args per command. Usually mod name(s).

.PARAMETER Description
    For 'new': mod description.

.PARAMETER PzInstallDir
    For 'init': override PZ install path (else read from mods.json or prompt).

.PARAMETER Force
    Allow destructive ops that would otherwise refuse (overwrite src-pristine, etc.)

.PARAMETER DryRun
    Print planned filesystem changes without touching disk. Supported by install/uninstall.
#>
[CmdletBinding()]
param(
    [Parameter(Position=0, Mandatory=$true)]
    [ValidateSet('init','new','list','status','enter','capture','diff','reset','install','uninstall','verify','resync-pristine','help')]
    [string]$Command,

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Rest,

    [string]$Description,
    [string]$PzInstallDir,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$Root = $PSScriptRoot
. (Join-Path $Root 'lib\mod-lib.ps1')

# -----------------------------------------------------------------------------
# Config / paths — lazily loaded because 'init' runs before .mod-config.json exists.
# -----------------------------------------------------------------------------
$script:_cfg = $null
function Get-Cfg {
    if (-not $script:_cfg) { $script:_cfg = Read-ModConfig -RootDir $Root }
    return $script:_cfg
}

function Ensure-Initialized {
    $c = Get-Cfg
    if (-not (Test-Path -LiteralPath $c.PristineDir)) {
        throw "src-pristine/ not found. Run: ./mod.ps1 init"
    }
    return $c
}

function Ensure-ModExists {
    param([string]$Name)
    $c = Get-Cfg
    $dir = Join-Path $c.ModsDir $Name
    if (-not (Test-Path -LiteralPath $dir)) { throw "mod '$Name' not found at $dir" }
    return $dir
}

function Get-AllMods {
    $c = Get-Cfg
    if (-not (Test-Path -LiteralPath $c.ModsDir)) { return @() }
    return Get-ChildItem -LiteralPath $c.ModsDir -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name | ForEach-Object { $_.Name }
}

# -----------------------------------------------------------------------------
# init — full workspace bootstrap. Idempotent; use -Force to redo each step.
#
#   Steps:
#     1. Resolve pzInstallDir (flag > .mod-config.json > default Steam path).
#     2. Check tools: java.exe, jar.exe, git.exe.
#     3. Download tools/vineflower.jar if missing.
#     4. Copy PZ top-level *.jar -> libs/.
#     5. Copy PZ class subtrees (zombie, astar, com, de, fmod, javax, org, se)
#        -> classes-original/.
#     6. Rejar each class subtree into libs/classpath-originals/<name>.jar.
#     7. Write .mod-config.json.
#     8. Decompile classes-original/zombie -> src-pristine/zombie (Vineflower).
#     9. Ensure mods/ and .mod-state.json exist.
# -----------------------------------------------------------------------------

$script:VineflowerVersion = '1.11.1'
$script:VineflowerUrl     = "https://github.com/Vineflower/vineflower/releases/download/$script:VineflowerVersion/vineflower-$script:VineflowerVersion.jar"
$script:PzClassSubtrees   = @('zombie','astar','com','de','fmod','javax','org','se')
$script:DefaultPzInstall  = 'C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid'

function _InitResolvePzInstall {
    # Priority: -PzInstallDir flag > .mod-config.json > default Steam path.
    if ($PzInstallDir) { return (Expand-ConfigPath $PzInstallDir $Root) }
    $configPath = Join-Path $Root '.mod-config.json'
    if (Test-Path -LiteralPath $configPath) {
        $raw = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($raw.pzInstallDir) { return (Expand-ConfigPath $raw.pzInstallDir $Root) }
    }
    if (Test-Path -LiteralPath $script:DefaultPzInstall) {
        Write-Host "  using default PZ install: $script:DefaultPzInstall"
        return $script:DefaultPzInstall
    }
    throw "could not locate PZ install. Pass -PzInstallDir '...'"
}

function _InitStep_Tools {
    $java = Get-Command java.exe -ErrorAction SilentlyContinue
    if (-not $java) { throw "java.exe not found on PATH (need JDK 17+)" }
    $jar  = Get-Command jar.exe -ErrorAction SilentlyContinue
    if (-not $jar)  { throw "jar.exe not found on PATH (ships with JDK)" }
    [void](Test-GitAvailable)
    Write-Host "  java: $($java.Source)"
    Write-Host "  jar:  $($jar.Source)"
}

function _InitStep_Vineflower {
    $toolsDir = Join-Path $Root 'tools'
    $target   = Join-Path $toolsDir 'vineflower.jar'
    if ((Test-Path -LiteralPath $target) -and -not $Force) {
        Write-Host "  [skip] $target already exists"
        return $target
    }
    if (-not (Test-Path -LiteralPath $toolsDir)) { New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null }
    Write-Host "  downloading $script:VineflowerUrl"
    try {
        Invoke-WebRequest -Uri $script:VineflowerUrl -OutFile $target -UseBasicParsing
    } catch {
        throw "vineflower download failed: $_"
    }
    $size = (Get-Item -LiteralPath $target).Length
    Write-Host "  wrote $target ($([int]($size/1024)) KB)"
    return $target
}

function _InitStep_CopyPzJars {
    param([string]$PzDir)
    $libsDir = Join-Path $Root 'libs'
    if (-not (Test-Path -LiteralPath $libsDir)) { New-Item -ItemType Directory -Force -Path $libsDir | Out-Null }
    # Top-level jars only (skip jre/, jre64/, etc.)
    $srcJars = Get-ChildItem -LiteralPath $PzDir -Filter *.jar -File -ErrorAction SilentlyContinue
    if ($srcJars.Count -eq 0) { throw "no top-level .jar files under $PzDir — is this the correct PZ install?" }
    $copied = 0; $skipped = 0
    foreach ($j in $srcJars) {
        $dst = Join-Path $libsDir $j.Name
        if ((Test-Path -LiteralPath $dst) -and -not $Force) {
            $srcHash = Get-FileHash256 $j.FullName
            $dstHash = Get-FileHash256 $dst
            if ($srcHash -eq $dstHash) { $skipped++; continue }
        }
        Copy-Item -LiteralPath $j.FullName -Destination $dst -Force
        $copied++
    }
    Write-Host "  libs/: copied $copied, unchanged $skipped (total $($srcJars.Count))"
}

function _InitStep_CopyPzClasses {
    param([string]$PzDir)
    $dstRoot = Join-Path $Root 'classes-original'
    if (-not (Test-Path -LiteralPath $dstRoot)) { New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null }
    foreach ($subtree in $script:PzClassSubtrees) {
        $src = Join-Path $PzDir $subtree
        $dst = Join-Path $dstRoot $subtree
        if (-not (Test-Path -LiteralPath $src)) {
            Write-Warning "  [missing] $src — skipping"
            continue
        }
        if ((Test-Path -LiteralPath $dst) -and -not $Force) {
            Write-Host "  [skip] classes-original/$subtree (use -Force to refresh)"
            continue
        }
        Write-Host "  classes-original/$subtree <- $src"
        Copy-Tree-Mirror -Src $src -Dst $dst
    }
}

function _InitStep_RejarOriginals {
    $dstRoot = Join-Path $Root 'libs\classpath-originals'
    if (-not (Test-Path -LiteralPath $dstRoot)) { New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null }
    foreach ($subtree in $script:PzClassSubtrees) {
        $classDir = Join-Path $Root "classes-original\$subtree"
        if (-not (Test-Path -LiteralPath $classDir)) { continue }
        $jarPath = Join-Path $dstRoot "$subtree.jar"
        # Skip if jar exists and is newer than every .class (unless -Force).
        if ((Test-Path -LiteralPath $jarPath) -and -not $Force) {
            $jarMtime = (Get-Item -LiteralPath $jarPath).LastWriteTimeUtc
            $newest = (Get-ChildItem -LiteralPath $classDir -Recurse -File |
                       Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
            if ($newest -and $newest -le $jarMtime) {
                Write-Host "  [skip] libs/classpath-originals/$subtree.jar (up to date)"
                continue
            }
        }
        Write-Host "  libs/classpath-originals/$subtree.jar <- classes-original/$subtree"
        Push-Location -LiteralPath (Join-Path $Root 'classes-original')
        try {
            & jar.exe cf $jarPath $subtree
            if ($LASTEXITCODE -ne 0) { throw "jar failed for $subtree (exit $LASTEXITCODE)" }
        } finally { Pop-Location }
    }
}

function _InitStep_WriteConfig {
    param([string]$PzInstall)
    $configPath = Join-Path $Root '.mod-config.json'
    if ((Test-Path -LiteralPath $configPath) -and -not $Force) {
        $existing = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $existingExpanded = if ($existing.pzInstallDir) { Expand-ConfigPath $existing.pzInstallDir $Root } else { '' }
        if ($existingExpanded -eq $PzInstall) {
            Write-Host "  [skip] .mod-config.json (pzInstallDir unchanged)"
            return
        }
    }
    Write-ModConfig -RootDir $Root -PzInstallDir $PzInstall -OriginalsDir 'classes-original'
    Write-Host "  wrote .mod-config.json (pzInstallDir=$PzInstall)"
}

function _InitStep_Decompile {
    $pristineDir = Join-Path $Root 'src-pristine'
    $classesOrig = Join-Path $Root 'classes-original\zombie'
    $vineflower  = Join-Path $Root 'tools\vineflower.jar'

    if (-not (Test-Path -LiteralPath $classesOrig)) {
        throw "classes-original/zombie/ not found at $classesOrig (step 5 should have populated it)"
    }
    if ((Test-Path -LiteralPath $pristineDir) -and -not $Force) {
        Write-Host "  [skip] src-pristine/ already exists (use -Force to regenerate)"
        return
    }
    if (Test-Path -LiteralPath $pristineDir) {
        Write-Host "  wiping existing src-pristine/"
        Remove-Item -Recurse -Force -LiteralPath $pristineDir
    }

    $libsDir  = Join-Path $Root 'libs'
    $origJars = Join-Path $Root 'libs\classpath-originals'
    $jars = @()
    $jars += Get-ChildItem -LiteralPath $libsDir  -Filter *.jar -ErrorAction SilentlyContinue
    $jars += Get-ChildItem -LiteralPath $origJars -Filter *.jar -ErrorAction SilentlyContinue
    if ($jars.Count -eq 0) { throw "no jars in libs/ or libs/classpath-originals/" }

    $tmpOut = Join-Path $Root 'src-pristine-tmp'
    if (Test-Path -LiteralPath $tmpOut) { Remove-Item -Recurse -Force -LiteralPath $tmpOut }
    New-Item -ItemType Directory -Force -Path $tmpOut | Out-Null

    $vArgs = @('-jar', $vineflower, '--silent')
    foreach ($j in $jars) { $vArgs += "-e=$($j.FullName)" }
    $vArgs += $classesOrig
    $vArgs += $tmpOut

    Write-Host "  decompiling classes-original/zombie -> src-pristine/zombie (Vineflower, ~1 min)..."
    & java.exe @vArgs
    if ($LASTEXITCODE -ne 0) { throw "Vineflower failed (exit $LASTEXITCODE)" }

    New-Item -ItemType Directory -Force -Path $pristineDir | Out-Null
    Move-Item -LiteralPath $tmpOut -Destination (Join-Path $pristineDir 'zombie')
    $count = (Get-ChildItem -LiteralPath (Join-Path $pristineDir 'zombie') -Recurse -Filter *.java -File).Count
    Write-Host "  src-pristine/zombie/: $count .java files"
}

function _InitStep_Scaffold {
    $modsDir   = Join-Path $Root 'mods'
    $stateFile = Join-Path $Root '.mod-state.json'
    if (-not (Test-Path -LiteralPath $modsDir))   { New-Item -ItemType Directory -Force -Path $modsDir | Out-Null; Write-Host "  created mods/" }
    if (-not (Test-Path -LiteralPath $stateFile)) {
        Write-ModState -StateFile $stateFile -State ([pscustomobject]@{
            version = 1; stack = @(); installedAt = $null; installed = @()
        })
        Write-Host "  created .mod-state.json"
    }
}

function Cmd-Init {
    Write-Host "==> step 1/9: resolve PZ install path"
    $pz = _InitResolvePzInstall
    if (-not (Test-Path -LiteralPath $pz)) {
        throw "PZ install dir does not exist: $pz"
    }
    Write-Host "  $pz"

    Write-Host ""
    Write-Host "==> step 2/9: tools check (java, jar, git)"
    _InitStep_Tools

    Write-Host ""
    Write-Host "==> step 3/9: vineflower.jar ($script:VineflowerVersion)"
    [void](_InitStep_Vineflower)

    Write-Host ""
    Write-Host "==> step 4/9: copy PZ jars -> libs/"
    _InitStep_CopyPzJars -PzDir $pz

    Write-Host ""
    Write-Host "==> step 5/9: copy PZ class trees -> classes-original/"
    _InitStep_CopyPzClasses -PzDir $pz

    Write-Host ""
    Write-Host "==> step 6/9: rejar class trees -> libs/classpath-originals/"
    _InitStep_RejarOriginals

    Write-Host ""
    Write-Host "==> step 7/9: write .mod-config.json"
    _InitStep_WriteConfig -PzInstall $pz

    Write-Host ""
    Write-Host "==> step 8/9: decompile zombie -> src-pristine/"
    _InitStep_Decompile

    Write-Host ""
    Write-Host "==> step 9/9: scaffold mods/ + .mod-state.json"
    _InitStep_Scaffold

    # Invalidate cached cfg — it was populated before config was guaranteed written.
    $script:_cfg = $null

    Write-Host ""
    Write-Host "init complete."
    Write-Host "  next: ./mod.ps1 new <mod-name>  then  ./mod.ps1 capture <mod-name>"
}

# -----------------------------------------------------------------------------
# new — create mods/<name>/
# -----------------------------------------------------------------------------
function Cmd-New {
    param([string]$Name)
    if (-not $Name) { throw "usage: ./mod.ps1 new <name> [-Description '...']" }
    $c = Ensure-Initialized
    $dir = Join-Path $c.ModsDir $Name
    if (Test-Path -LiteralPath $dir) { throw "mod '$Name' already exists at $dir" }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $dir 'patches') | Out-Null
    $now = (Get-Date).ToUniversalTime().ToString("o")
    $obj = [ordered]@{
        name             = $Name
        description      = ($Description ?? '')
        version          = '0.1.0'
        createdAt        = $now
        updatedAt        = $now
        pristineSnapshot = ''
    }
    Write-ModJson -ModDir $dir -ModObj ([pscustomobject]$obj)
    Write-Host "created mod: $dir"
}

# -----------------------------------------------------------------------------
# list
# -----------------------------------------------------------------------------
function Cmd-List {
    $c = Get-Cfg
    if (-not (Test-Path -LiteralPath $c.ModsDir)) {
        Write-Host "(no mods directory; run ./mod.ps1 init)"
        return
    }
    $mods = @(Get-AllMods)
    if ($mods.Count -eq 0) { Write-Host "(no mods defined)"; return }
    "{0,-24} {1,6} {2,4} {3,4}  {4}" -f 'MOD', 'PATCH', 'NEW', 'DEL', 'DESCRIPTION'
    "{0,-24} {1,6} {2,4} {3,4}  {4}" -f '---', '-----', '---', '---', '-----------'
    foreach ($m in $mods) {
        $dir = Join-Path $c.ModsDir $m
        $mj = Read-ModJson -ModDir $dir
        $items = Get-ModPatchItems -ModDir $dir
        $nP = @($items | Where-Object { $_.Kind -eq 'patch'  }).Count
        $nN = @($items | Where-Object { $_.Kind -eq 'new'    }).Count
        $nD = @($items | Where-Object { $_.Kind -eq 'delete' }).Count
        $desc = if ($mj.PSObject.Properties['description']) { $mj.description } else { '' }
        "{0,-24} {1,6} {2,4} {3,4}  {4}" -f $m, $nP, $nN, $nD, $desc
    }
}

# -----------------------------------------------------------------------------
# status — working tree vs pristine, or per-mod patch applicability
# -----------------------------------------------------------------------------
function Cmd-Status {
    param([string]$Name)
    $c = Ensure-Initialized
    if ($Name) {
        $dir = Ensure-ModExists $Name
        $mj = Read-ModJson -ModDir $dir
        $items = Get-ModPatchItems -ModDir $dir
        Write-Host "mod: $Name"
        if ($mj.PSObject.Properties['description']) { Write-Host "  desc: $($mj.description)" }
        Write-Host "  patches: $(@($items | ? Kind -eq 'patch').Count) new: $(@($items | ? Kind -eq 'new').Count) delete: $(@($items | ? Kind -eq 'delete').Count)"
        if ($items.Count -eq 0) { return }

        # Check patch applicability against current pristine.
        $scratch = Join-Path $c.RootDir "build\stage-scratch-status-$Name"
        New-EmptyDir $scratch
        try {
            foreach ($it in $items) {
                switch ($it.Kind) {
                    'patch' {
                        $theirs = New-PatchedTheirsFile -PristineDir $c.PristineDir -ScratchDir $scratch -PatchFile $it.File -RelPath $it.Rel
                        $tag = if ($theirs) { 'ok' } else { 'STALE' }
                        "  {0,-6} {1} ({2})" -f $tag, $it.Rel, $it.Kind
                    }
                    default {
                        "  {0,-6} {1} ({2})" -f '-', $it.Rel, $it.Kind
                    }
                }
            }
        } finally {
            if (Test-Path -LiteralPath $scratch) { Remove-Item -Recurse -Force -LiteralPath $scratch }
        }
        return
    }

    # No-arg: working tree status.
    Write-Host "working tree: $($c.SrcDir)"
    $diverged = @()
    $pristineZ = Join-Path $c.PristineDir 'zombie'
    $srcZ      = Join-Path $c.SrcDir      'zombie'
    if (-not (Test-Path -LiteralPath $srcZ)) {
        Write-Host "  (src/zombie/ missing — consider ./mod.ps1 reset)"
    } else {
        Get-ChildItem -LiteralPath $srcZ -Recurse -Filter *.java -File | ForEach-Object {
            $rel = 'zombie/' + ([System.IO.Path]::GetRelativePath($srcZ, $_.FullName) -replace '\\','/')
            $p = Join-Path $c.PristineDir $rel
            if (-not (Test-Path -LiteralPath $p)) { $diverged += "+ $rel"; return }
            if ((Get-FileHash256 $_.FullName) -ne (Get-FileHash256 $p)) { $diverged += "M $rel" }
        }
        Get-ChildItem -LiteralPath $pristineZ -Recurse -Filter *.java -File | ForEach-Object {
            $rel = 'zombie/' + ([System.IO.Path]::GetRelativePath($pristineZ, $_.FullName) -replace '\\','/')
            $s = Join-Path $c.SrcDir $rel
            if (-not (Test-Path -LiteralPath $s)) { $diverged += "- $rel" }
        }
    }
    if ($diverged.Count -eq 0) {
        Write-Host "  clean (matches src-pristine)"
    } else {
        Write-Host "  $($diverged.Count) diverging file(s):"
        $diverged | Sort-Object | ForEach-Object { "    $_" }
    }
    if (Test-Path -LiteralPath $c.EnterFile) {
        $enter = Get-Content -LiteralPath $c.EnterFile -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host "  entered stack: $($enter.stack -join ', ')"
    }
    # Install state.
    $state = Read-ModState -StateFile $c.StateFile
    if ($state.installed.Count -gt 0) {
        Write-Host ""
        Write-Host "installed stack: $($state.stack -join ', ')  ($($state.installed.Count) class files)"
    }
}

# -----------------------------------------------------------------------------
# reset — robocopy /MIR pristine -> src
# -----------------------------------------------------------------------------
function Cmd-Reset {
    $c = Ensure-Initialized
    $srcZ = Join-Path $c.SrcDir 'zombie'
    $priZ = Join-Path $c.PristineDir 'zombie'
    Write-Host "reset: src/zombie/ <- src-pristine/zombie/"
    Copy-Tree-Mirror -Src $priZ -Dst $srcZ
    if (Test-Path -LiteralPath $c.EnterFile) { Remove-Item -LiteralPath $c.EnterFile -Force }
    Write-Host "done."
}

# -----------------------------------------------------------------------------
# enter — reset src, apply a stack of mods sequentially (with 3-way fallback)
# -----------------------------------------------------------------------------
function Cmd-Enter {
    param([string[]]$Stack)
    if (-not $Stack -or $Stack.Count -eq 0) { throw "usage: ./mod.ps1 enter <mod1> [mod2 ...]" }
    $c = Ensure-Initialized
    foreach ($m in $Stack) { [void](Ensure-ModExists $m) }

    $srcZ = Join-Path $c.SrcDir 'zombie'
    $priZ = Join-Path $c.PristineDir 'zombie'
    Write-Host "enter [$($Stack -join ', ')]: reset src/ then apply patches"
    Copy-Tree-Mirror -Src $priZ -Dst $srcZ

    $result = Apply-StackToWorkTree -Stack $Stack -WorkDir $c.SrcDir -PristineDir $c.PristineDir -ScratchRoot (Join-Path $c.RootDir 'build\stage-scratch-enter')
    if ($result.Conflicts.Count -gt 0) {
        Write-Host ""
        Write-Host "CONFLICTS:" -ForegroundColor Red
        foreach ($cf in $result.Conflicts) {
            "  {0}  [{1}]  mods: {2}" -f $cf.Rel, $cf.Type, ($cf.Mods -join ', ')
        }
        throw "enter failed — resolve conflicts (or adjust stack order) and retry"
    }
    # Record entered stack.
    ([pscustomobject]@{ stack = $Stack; enteredAt = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json) | Set-Content -LiteralPath $c.EnterFile -Encoding UTF8
    Write-Host "applied: $($result.Touched) file(s). Edit under src/zombie/; run capture when done."
}

# Apply a stack of mods into $WorkDir (which already mirrors pristine).
# Returns @{ Conflicts=@([...]); Touched=<int>; Deletes=@(rels) }
function Apply-StackToWorkTree {
    param(
        [string[]]$Stack,
        [string]$WorkDir,       # e.g. src/ or build/stage-src/
        [string]$PristineDir,   # src-pristine/
        [string]$ScratchRoot
    )
    $c = Get-Cfg
    $conflicts = New-Object System.Collections.ArrayList
    $touched = @{}
    $newOwner = @{}
    $deletes = New-Object System.Collections.ArrayList
    New-EmptyDir $ScratchRoot
    foreach ($mod in $Stack) {
        $dir = Join-Path $c.ModsDir $mod
        $items = Get-ModPatchItems -ModDir $dir
        $scratchDir = Join-Path $ScratchRoot $mod
        New-Item -ItemType Directory -Force -Path $scratchDir | Out-Null
        foreach ($it in $items) {
            switch ($it.Kind) {
                'new' {
                    if ($newOwner.ContainsKey($it.Rel)) {
                        [void]$conflicts.Add([pscustomobject]@{ Rel = $it.Rel; Type = 'new-collision'; Mods = @($newOwner[$it.Rel], $mod) })
                        continue
                    }
                    $dst = Join-Path $WorkDir $it.Rel
                    if (Test-Path -LiteralPath $dst) {
                        [void]$conflicts.Add([pscustomobject]@{ Rel = $it.Rel; Type = 'new-overwrites-existing'; Mods = @($mod) })
                        continue
                    }
                    $parent = Split-Path $dst -Parent
                    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
                    Copy-Item -LiteralPath $it.File -Destination $dst -Force
                    $newOwner[$it.Rel] = $mod
                    $touched[$it.Rel] = $mod
                }
                'delete' {
                    $dst = Join-Path $WorkDir $it.Rel
                    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
                    [void]$deletes.Add($it.Rel)
                    $touched[$it.Rel] = $mod
                }
                'patch' {
                    $target = Join-Path $WorkDir $it.Rel
                    $pristineFile = Join-Path $PristineDir $it.Rel
                    if (-not (Test-Path -LiteralPath $pristineFile)) {
                        [void]$conflicts.Add([pscustomobject]@{ Rel = $it.Rel; Type = 'patch-missing-pristine'; Mods = @($mod) })
                        continue
                    }
                    if (-not (Test-Path -LiteralPath $target)) {
                        # File was deleted by an earlier mod — patch can't apply.
                        [void]$conflicts.Add([pscustomobject]@{ Rel = $it.Rel; Type = 'patch-target-missing'; Mods = @($mod) })
                        continue
                    }
                    if (-not $touched.ContainsKey($it.Rel)) {
                        # Fast path: target still matches pristine, apply directly.
                        if (Invoke-GitApplyFile -PatchFile $it.File -WorkDir $WorkDir -RelPath $it.Rel) {
                            $touched[$it.Rel] = $mod
                            continue
                        }
                        # Fall through to 3-way for robustness.
                    }
                    # 3-way: generate theirs from pristine, merge with current target.
                    $theirs = New-PatchedTheirsFile -PristineDir $PristineDir -ScratchDir $scratchDir -PatchFile $it.File -RelPath $it.Rel
                    if (-not $theirs) {
                        [void]$conflicts.Add([pscustomobject]@{ Rel = $it.Rel; Type = 'patch-does-not-apply-to-pristine'; Mods = @($mod) })
                        continue
                    }
                    $ok = Invoke-GitMergeFile -Current $target -Base $pristineFile -Incoming $theirs
                    if (-not $ok) {
                        $prevMod = if ($touched.ContainsKey($it.Rel)) { $touched[$it.Rel] } else { '(pristine)' }
                        [void]$conflicts.Add([pscustomobject]@{ Rel = $it.Rel; Type = 'merge-conflict'; Mods = @($prevMod, $mod) })
                        continue
                    }
                    $touched[$it.Rel] = $mod
                }
            }
        }
    }
    if (Test-Path -LiteralPath $ScratchRoot) { Remove-Item -Recurse -Force -LiteralPath $ScratchRoot }
    return [pscustomobject]@{
        Conflicts = @($conflicts)
        Touched   = $touched.Count
        TouchedMap = $touched
        Deletes   = @($deletes | Select-Object -Unique)
    }
}

# -----------------------------------------------------------------------------
# capture — diff src/ vs pristine, rewrite mods/<name>/patches/
# -----------------------------------------------------------------------------
function Cmd-Capture {
    param([string]$Name)
    if (-not $Name) { throw "usage: ./mod.ps1 capture <name>" }
    $c = Ensure-Initialized
    $dir = Ensure-ModExists $Name
    $patchesDir = Join-Path $dir 'patches'
    [void](Test-GitAvailable)

    Write-Host "capture ${Name}: diffing src/ vs src-pristine/"
    # Wipe existing patches — current src state is the source of truth.
    if (Test-Path -LiteralPath $patchesDir) { Remove-Item -Recurse -Force -LiteralPath $patchesDir }
    New-Item -ItemType Directory -Force -Path $patchesDir | Out-Null

    $srcZ = Join-Path $c.SrcDir 'zombie'
    $priZ = Join-Path $c.PristineDir 'zombie'
    if (-not (Test-Path -LiteralPath $srcZ)) { throw "src/zombie/ not found" }

    $touched = @()  # list of Rel under zombie/ that this mod references (for snapshot)

    # Modified + new files.
    Get-ChildItem -LiteralPath $srcZ -Recurse -Filter *.java -File | ForEach-Object {
        $relUnder = [System.IO.Path]::GetRelativePath($srcZ, $_.FullName) -replace '\\','/'
        $rel = "zombie/$relUnder"
        $pristineFile = Join-Path $c.PristineDir $rel
        if (-not (Test-Path -LiteralPath $pristineFile)) {
            # New file — full copy.
            $dst = Join-Path $patchesDir "$rel.new"
            $parent = Split-Path $dst -Parent
            if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
            Write-Host "  new:  $rel"
            $touched += $rel
            return
        }
        $hSrc = Get-FileHash256 $_.FullName
        $hPri = Get-FileHash256 $pristineFile
        if ($hSrc -eq $hPri) { return }
        $dst = Join-Path $patchesDir "$rel.patch"
        $wrote = Write-UnifiedDiff -PristineFile $pristineFile -WorkingFile $_.FullName -RelPath $rel -OutPath $dst
        if ($wrote) {
            Write-Host "  mod:  $rel"
            $touched += $rel
        }
    }

    # Deleted files (in pristine but not in src).
    Get-ChildItem -LiteralPath $priZ -Recurse -Filter *.java -File | ForEach-Object {
        $relUnder = [System.IO.Path]::GetRelativePath($priZ, $_.FullName) -replace '\\','/'
        $rel = "zombie/$relUnder"
        $s = Join-Path $c.SrcDir $rel
        if (-not (Test-Path -LiteralPath $s)) {
            $dst = Join-Path $patchesDir "$rel.delete"
            $parent = Split-Path $dst -Parent
            if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            [System.IO.File]::WriteAllBytes($dst, @())
            Write-Host "  del:  $rel"
            $touched += $rel
        }
    }

    # Update mod.json with new snapshot + updatedAt.
    $mj = Read-ModJson -ModDir $dir
    $items = Get-ModPatchItems -ModDir $dir
    $snap = Get-PristineSnapshotForMod -PristineDir $c.PristineDir -Items $items
    $mj | Add-Member -NotePropertyName updatedAt -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
    $mj | Add-Member -NotePropertyName pristineSnapshot -NotePropertyValue $snap -Force
    Write-ModJson -ModDir $dir -ModObj $mj

    Write-Host "captured $($touched.Count) file(s) into $patchesDir"
}

# -----------------------------------------------------------------------------
# diff — cat all patches for a mod
# -----------------------------------------------------------------------------
function Cmd-Diff {
    param([string]$Name)
    if (-not $Name) { throw "usage: ./mod.ps1 diff <name>" }
    $dir = Ensure-ModExists $Name
    $items = Get-ModPatchItems -ModDir $dir
    foreach ($it in $items) {
        Write-Host ""
        Write-Host "=== $($it.Rel) [$($it.Kind)] ===" -ForegroundColor Cyan
        Get-Content -LiteralPath $it.File -Encoding UTF8
    }
}

# -----------------------------------------------------------------------------
# install — atomic stage+compile+install
# -----------------------------------------------------------------------------
function Cmd-Install {
    param([string[]]$Stack)
    if (-not $Stack -or $Stack.Count -eq 0) { throw "usage: ./mod.ps1 install <mod1> [mod2 ...]" }
    $c = Ensure-Initialized
    [void](Test-GitAvailable)
    foreach ($m in $Stack) { [void](Ensure-ModExists $m) }

    # --- Phase 1: stage source ---
    Write-Host "==> stage source ($($c.StageDir))"
    $stageZ = Join-Path $c.StageDir 'zombie'
    if (Test-Path -LiteralPath $c.StageDir) { Remove-Item -Recurse -Force -LiteralPath $c.StageDir }
    New-Item -ItemType Directory -Force -Path $c.StageDir | Out-Null
    Copy-Tree-Mirror -Src (Join-Path $c.PristineDir 'zombie') -Dst $stageZ

    $result = Apply-StackToWorkTree -Stack $Stack -WorkDir $c.StageDir -PristineDir $c.PristineDir -ScratchRoot (Join-Path $c.RootDir 'build\stage-scratch')
    if ($result.Conflicts.Count -gt 0) {
        Write-Host ""
        Write-Host "CONFLICTS — install aborted, PZ install untouched:" -ForegroundColor Red
        foreach ($cf in $result.Conflicts) {
            "  {0}  [{1}]  mods: {2}" -f $cf.Rel, $cf.Type, ($cf.Mods -join ', ')
        }
        Remove-Item -Recurse -Force -LiteralPath $c.StageDir -ErrorAction SilentlyContinue
        throw "conflicts detected"
    }
    Write-Host "  applied: $($result.Touched) file(s)"

    # --- Phase 2: compile touched .java files ---
    $javaFiles = $result.TouchedMap.Keys | ForEach-Object { Join-Path $c.StageDir $_ }
    $javaFiles = @($javaFiles | Where-Object { $_ -like '*.java' -and (Test-Path -LiteralPath $_) })
    if ($javaFiles.Count -eq 0 -and $result.Deletes.Count -eq 0) {
        Remove-Item -Recurse -Force -LiteralPath $c.StageDir -ErrorAction SilentlyContinue
        throw "no files to install (no patches/new/delete in requested stack)"
    }
    if ($javaFiles.Count -gt 0) {
        Write-Host ""
        Write-Host "==> compile $($javaFiles.Count) file(s) via build.ps1 -Clean"
        $buildScript = Join-Path $c.RootDir 'build.ps1'
        try {
            & $buildScript -Clean -Files $javaFiles
        } catch {
            Remove-Item -Recurse -Force -LiteralPath $c.StageDir -ErrorAction SilentlyContinue
            throw "build.ps1 failed: $_  PZ install untouched."
        }
    }

    # --- Phase 3: restore any prior-install to pristine ---
    Write-Host ""
    Write-Host "==> restore prior install to original"
    Restore-InstalledClasses -Cfg $c

    # --- Phase 4: copy new class files to PZ install ---
    Write-Host ""
    Write-Host "==> copy class files to $($c.PzInstallDir)"
    $installedList = New-Object System.Collections.ArrayList
    foreach ($rel in $result.TouchedMap.Keys) {
        if (-not $rel.EndsWith('.java')) { continue }
        $base = $rel.Substring(0, $rel.Length - '.java'.Length)   # e.g. zombie/Lua/Event
        $classDirRel = Split-Path $base -Parent                    # zombie/Lua
        $leafBase = Split-Path $base -Leaf                         # Event
        $buildClassDir = Join-Path $c.BuildDir $classDirRel
        if (-not (Test-Path -LiteralPath $buildClassDir)) {
            # No class output for this java (compile error for just this file? shouldn't happen if compile succeeded).
            Write-Warning "no class output under $buildClassDir for $rel"
            continue
        }
        $matches = Get-ChildItem -LiteralPath $buildClassDir -File | Where-Object {
            $_.Name -eq "$leafBase.class" -or $_.Name -like "$leafBase`$*.class"
        }
        foreach ($cf in $matches) {
            $relClass = (Join-Path $classDirRel $cf.Name) -replace '\\','/'
            $dst = Join-Path $c.PzInstallDir ($relClass -replace '/','\')
            $parent = Split-Path $dst -Parent
            if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -LiteralPath $cf.FullName -Destination $dst -Force
            Write-Host "  + $relClass"
            [void]$installedList.Add([pscustomobject]@{
                rel = $relClass
                modOrigin = $result.TouchedMap[$rel]
                sha256 = Get-FileHash256 $cf.FullName
            })
        }
    }
    # Deletes: restore from classes-original/ if present, else delete in install.
    foreach ($rel in $result.Deletes) {
        $base = $rel.Substring(0, $rel.Length - '.java'.Length)
        $classDirRel = Split-Path $base -Parent
        $leafBase = Split-Path $base -Leaf
        $origClassDir = Join-Path $c.OriginalsDir $classDirRel
        if (-not (Test-Path -LiteralPath $origClassDir)) { continue }
        $candidates = Get-ChildItem -LiteralPath $origClassDir -File | Where-Object {
            $_.Name -eq "$leafBase.class" -or $_.Name -like "$leafBase`$*.class"
        }
        foreach ($orig in $candidates) {
            $relClass = (Join-Path $classDirRel $orig.Name) -replace '\\','/'
            $dst = Join-Path $c.PzInstallDir ($relClass -replace '/','\')
            if (Test-Path -LiteralPath $dst) {
                Remove-Item -LiteralPath $dst -Force
                Write-Host "  - $relClass (deleted — also removed from install)"
            }
        }
    }

    # --- Phase 5: write state ---
    Write-ModState -StateFile $c.StateFile -State ([pscustomobject]@{
        version = 1
        stack = @($Stack)
        installedAt = (Get-Date).ToUniversalTime().ToString('o')
        installed = @($installedList)
    })
    Write-Host ""
    Write-Host "install complete. stack=[$($Stack -join ', ')]  class files=$($installedList.Count)"
}

function Restore-InstalledClasses {
    param([object]$Cfg)
    $state = Read-ModState -StateFile $Cfg.StateFile
    if (-not $state.installed -or $state.installed.Count -eq 0) {
        Write-Host "  (nothing to restore)"
        return
    }
    foreach ($entry in $state.installed) {
        $rel = $entry.rel
        $installPath = Join-Path $Cfg.PzInstallDir ($rel -replace '/','\')
        $origPath    = Join-Path $Cfg.OriginalsDir ($rel -replace '/','\')
        if (Test-Path -LiteralPath $origPath) {
            $parent = Split-Path $installPath -Parent
            if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -LiteralPath $origPath -Destination $installPath -Force
            Write-Host "  restore: $rel"
        } elseif (Test-Path -LiteralPath $installPath) {
            Remove-Item -LiteralPath $installPath -Force
            Write-Host "  delete:  $rel (no pristine — was mod-added)"
        }
    }
}

# -----------------------------------------------------------------------------
# uninstall — restore everything in state, clear state
# -----------------------------------------------------------------------------
function Cmd-Uninstall {
    $c = Ensure-Initialized
    $state = Read-ModState -StateFile $c.StateFile
    if (-not $state.installed -or $state.installed.Count -eq 0) {
        Write-Host "nothing installed."
        return
    }
    Write-Host "uninstall: restoring $($state.installed.Count) class file(s)"
    Restore-InstalledClasses -Cfg $c
    Write-ModState -StateFile $c.StateFile -State ([pscustomobject]@{
        version = 1; stack = @(); installedAt = $null; installed = @()
    })
    Write-Host "done."
}

# -----------------------------------------------------------------------------
# verify — re-hash installed files, report drift
# -----------------------------------------------------------------------------
function Cmd-Verify {
    $c = Ensure-Initialized
    $state = Read-ModState -StateFile $c.StateFile
    if (-not $state.installed -or $state.installed.Count -eq 0) {
        Write-Host "no installed state."
        return
    }
    Write-Host "verify $($state.installed.Count) installed file(s)"
    $drift = 0
    foreach ($entry in $state.installed) {
        $rel = $entry.rel
        $installPath = Join-Path $c.PzInstallDir ($rel -replace '/','\')
        $actual = Get-FileHash256 $installPath
        if (-not $actual) {
            Write-Host "  MISSING: $rel" -ForegroundColor Yellow
            $drift++
        } elseif ($actual -ne $entry.sha256) {
            Write-Host "  DRIFT:   $rel" -ForegroundColor Yellow
            $drift++
        }
    }
    if ($drift -eq 0) { Write-Host "  all files match recorded state" }
    else { Write-Host "  $drift drifted file(s). Re-run './mod.ps1 install $($state.stack -join ' ')' to repair." -ForegroundColor Yellow }
}

# -----------------------------------------------------------------------------
# resync-pristine — after a PZ update: regenerate src-pristine, recompute snapshots.
# Does NOT auto-apply mods; if a mod's patches don't apply to new pristine,
# it's surfaced and the user re-enters/captures that mod manually.
# -----------------------------------------------------------------------------
function Cmd-ResyncPristine {
    $c = Ensure-Initialized
    Write-Host "resync-pristine: re-run init flow with -Force"
    $script:_cfg = $null  # force reload after init
    & $PSCommandPath init -Force
    $c = Get-Cfg

    # Check each mod's patches still apply.
    Write-Host ""
    Write-Host "checking mod patches against new pristine..."
    foreach ($mod in (Get-AllMods)) {
        $dir = Join-Path $c.ModsDir $mod
        $items = Get-ModPatchItems -ModDir $dir
        $scratch = Join-Path $c.RootDir "build\resync-scratch-$mod"
        New-EmptyDir $scratch
        try {
            $stale = @()
            foreach ($it in $items) {
                if ($it.Kind -ne 'patch') { continue }
                $theirs = New-PatchedTheirsFile -PristineDir $c.PristineDir -ScratchDir $scratch -PatchFile $it.File -RelPath $it.Rel
                if (-not $theirs) { $stale += $it.Rel }
            }
            if ($stale.Count -eq 0) {
                # Refresh snapshot in mod.json.
                $mj = Read-ModJson -ModDir $dir
                $snap = Get-PristineSnapshotForMod -PristineDir $c.PristineDir -Items $items
                $mj | Add-Member -NotePropertyName pristineSnapshot -NotePropertyValue $snap -Force
                Write-ModJson -ModDir $dir -ModObj $mj
                Write-Host "  ${mod}: OK ($($items.Count) item(s), snapshot refreshed)"
            } else {
                Write-Host "  ${mod}: STALE — needs manual recapture. Run: ./mod.ps1 enter $mod  (resolve conflicts)  ./mod.ps1 capture $mod" -ForegroundColor Yellow
                $stale | ForEach-Object { "    - $_" }
            }
        } finally {
            if (Test-Path -LiteralPath $scratch) { Remove-Item -Recurse -Force -LiteralPath $scratch }
        }
    }
}

# -----------------------------------------------------------------------------
# dispatch
# -----------------------------------------------------------------------------
function Get-RestArg { param([int]$Index = 0) if ($Rest -and $Rest.Count -gt $Index) { return $Rest[$Index] } else { return $null } }

switch ($Command) {
    'init'              { Cmd-Init }
    'new'               { Cmd-New    -Name (Get-RestArg 0) }
    'list'              { Cmd-List }
    'status'            { Cmd-Status -Name (Get-RestArg 0) }
    'enter'             { Cmd-Enter  -Stack $Rest }
    'capture'           { Cmd-Capture -Name (Get-RestArg 0) }
    'diff'              { Cmd-Diff   -Name (Get-RestArg 0) }
    'reset'             { Cmd-Reset }
    'install'           { Cmd-Install -Stack $Rest }
    'uninstall'         { Cmd-Uninstall }
    'verify'            { Cmd-Verify }
    'resync-pristine'   { Cmd-ResyncPristine }
    'help'              { Get-Help $PSCommandPath -Detailed }
}
