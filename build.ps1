<#
.SYNOPSIS
    Compile modded PZ sources against PZ classpath, targeting Java 17.

.DESCRIPTION
    Wrapper around javac that builds the classpath from libs/ and libs/classpath-originals/,
    and compiles only the files passed in (no -sourcepath — decompiled siblings don't round-trip).

.PARAMETER Files
    Source files to compile (e.g. src/zombie/Lua/Event.java). If omitted, the script errors —
    compiling all 1601 decompiled files produces thousands of errors.

.PARAMETER Clean
    Wipe build/classes before compiling.

.EXAMPLE
    ./build.ps1 src/zombie/Lua/Event.java src/zombie/Lua/LuaProfiler.java

.EXAMPLE
    ./build.ps1 src/zombie/Foo.java
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Files
)

$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
$Src  = Join-Path $Root 'src'
$Out  = Join-Path $Root 'build\classes'
$Libs = Join-Path $Root 'libs'
$Orig = Join-Path $Root 'libs\classpath-originals'

if (-not $Files -or $Files.Count -eq 0) {
    throw "no source files given. Pass specific files (decompiled siblings don't round-trip)."
}

if ($Clean -and (Test-Path $Out)) {
    Remove-Item -Recurse -Force $Out
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null

$jars = @()
$jars += Get-ChildItem -Path $Libs -Filter *.jar -ErrorAction SilentlyContinue
$jars += Get-ChildItem -Path $Orig -Filter *.jar -ErrorAction SilentlyContinue
if ($jars.Count -eq 0) {
    throw "no jars found in $Libs or $Orig"
}
$ClassPath = ($jars | ForEach-Object { $_.FullName }) -join ';'

# Resolve source files to absolute paths (javac is happier on Windows).
$absFiles = @()
foreach ($f in $Files) {
    $p = if ([System.IO.Path]::IsPathRooted($f)) { $f } else { Join-Path $Root $f }
    if (-not (Test-Path $p)) { throw "source not found: $f" }
    $absFiles += (Resolve-Path $p).Path
}

Write-Host ("Compiling {0} file(s) -> {1} (Java 17)" -f $absFiles.Count, $Out)
& javac --release 17 -encoding UTF-8 -cp $ClassPath -d $Out @absFiles
if ($LASTEXITCODE -ne 0) { throw "javac failed (exit $LASTEXITCODE)" }

Write-Host "Done."
