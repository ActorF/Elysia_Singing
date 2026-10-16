<#
.SYNOPSIS
Prepare this project's bundled Windows and dedicated WSL2 singing runtimes.
.DESCRIPTION
Verify release parts before extracting or importing them, then record the exact
project-owned WSL distribution. No existing user distribution or default WSL
setting is changed. The console lock also prevents setup during conversion.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$BundleDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProjectRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$Repository = 'ActorF/Elysia_Singing'
$WslExecutable = Join-Path $env:SystemRoot 'System32\wsl.exe'
$TarExecutable = Join-Path $env:SystemRoot 'System32\tar.exe'

function _Assert-ProjectPath([string]$Path) {
    # Both ancestry and reparse checks matter: a cache junction can otherwise
    # redirect archive extraction or cleanup outside the selected project.
    $absolute = [IO.Path]::GetFullPath($Path)
    if (-not $absolute.StartsWith($ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Working path escapes this project: $absolute"
    }
    $cursor = $absolute
    while ($cursor.Length -gt $ProjectRoot.Length) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Working paths must not contain links or junctions: $cursor"
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $absolute
}

function _ProjectDirectory([string]$Relative) {
    $path = _Assert-ProjectPath (Join-Path $ProjectRoot $Relative)
    [IO.Directory]::CreateDirectory($path) | Out-Null
    return $path
}

function _Hash([string]$Path) {
    $provider = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return [BitConverter]::ToString($provider.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $provider.Dispose() }
}

function _Valid-File([string]$Path, [long]$Size, [string]$Digest) {
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and
        (Get-Item -LiteralPath $Path).Length -eq $Size -and
        (_Hash $Path) -eq $Digest.ToLowerInvariant())
}

function _Native-Ready([string]$Root) {
    $required = @(
        'models\cache\GPT-SoVITS-v2-240821\runtime\python.exe',
        'models\cache\GPT-SoVITS-v2-240821\runtime\python39.dll',
        'models\cache\GPT-SoVITS-v2-240821\runtime\python39.zip',
        'models\cache\GPT-SoVITS-v2-240821\runtime\python39._pth',
        'models\cache\GPT-SoVITS-v2-240821\runtime\Lib\site-packages\torch\__init__.py',
        'models\cache\GPT-SoVITS-v2-240821\ffmpeg.exe',
        'models\cache\GPT-SoVITS-v2-240821\ffprobe.exe',
        'models\cache\singing-runtime\site-packages\demucs\separate.py',
        'models\cache\singing-runtime\torch\hub\checkpoints\955717e8-8726e21a.th'
    )
    foreach ($relative in $required) {
        $path = _Assert-ProjectPath (Join-Path $Root $relative)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0) {
            return $false
        }
    }
    return $true
}

function _Read-Manifest {
    $manifestPath = Join-Path $ProjectRoot 'release-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'release-manifest.json is missing. Download the complete release package.'
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.repository -cne $Repository -or
        $manifest.release_tag -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        throw 'The release manifest is not a supported Elysia Singing release.'
    }
    foreach ($kind in @('windows', 'linux')) {
        $bundle = $manifest.bundles.$kind
        if ($bundle.archive -cne "$kind-runtime.tar.gz" -or $bundle.size -le 0 -or
            $bundle.sha256 -cnotmatch '^[a-fA-F0-9]{64}$' -or @($bundle.parts).Count -lt 1) {
            throw "Invalid $kind archive manifest."
        }
        $names = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        [long]$total = 0
        foreach ($part in $bundle.parts) {
            if ($part.name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -or
                $part.size -le 0 -or $part.size -ge 2GB -or
                $part.sha256 -cnotmatch '^[a-fA-F0-9]{64}$' -or -not $names.Add($part.name)) {
                throw "Invalid $kind release part."
            }
            $total += [long]$part.size
        }
        if ($total -ne [long]$bundle.size) { throw "The $kind part sizes do not match its archive." }
    }
    return $manifest
}

function _Run-Checked([string]$Executable, [string[]]$Arguments, [string]$Label) {
    # Windows PowerShell treats redirected native stderr as ErrorRecord objects.
    # Capture it without terminating before the actual native exit code is read.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $Executable @Arguments 2>&1)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    $output = (($lines | ForEach-Object { "$_" }) -join "`n").Replace([string][char]0, '')
    if ($code -ne 0) {
        if ($output.Length -gt 2000) { $output = $output.Substring($output.Length - 2000) }
        throw "$Label failed (exit $code). $output"
    }
    return $output
}

function _Get-Archive($Bundle, [string]$Tag) {
    $downloadRoot = _ProjectDirectory 'models\downloads'
    $archive = _Assert-ProjectPath (Join-Path $downloadRoot $Bundle.archive)
    if (_Valid-File $archive $Bundle.size $Bundle.sha256) { return $archive }
    $sources = New-Object 'System.Collections.Generic.List[string]'
    foreach ($part in $Bundle.parts) {
        if ($BundleDirectory) {
            $path = Join-Path $BundleDirectory $part.name
            if (-not (_Valid-File $path $part.size $part.sha256)) {
                throw "Missing or invalid local release part: $path"
            }
        } else {
            $path = _Assert-ProjectPath (Join-Path $downloadRoot $part.name)
            if (-not (_Valid-File $path $part.size $part.sha256)) {
                $temporary = _Assert-ProjectPath ($path + '.download')
                $url = "https://github.com/$Repository/releases/download/$Tag/$($part.name)"
                for ($attempt = 1; $attempt -le 3; $attempt++) {
                    try {
                        Write-Host "Downloading $($part.name) (attempt $attempt/3)..."
                        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                        Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $temporary -TimeoutSec 3600
                        if (-not (_Valid-File $temporary $part.size $part.sha256)) {
                            throw "Downloaded release part failed its size/SHA-256 check: $($part.name)"
                        }
                        Move-Item -LiteralPath $temporary -Destination $path -Force
                        break
                    } catch {
                        if ($attempt -eq 3) { throw }
                        Start-Sleep -Seconds (2 * $attempt)
                    }
                }
            }
        }
        $sources.Add($path)
    }
    Write-Host "Verifying and assembling $($Bundle.archive)..."
    $assembling = _Assert-ProjectPath ($archive + '.assembling')
    $output = [IO.File]::Open($assembling, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        foreach ($path in $sources) {
            $inputStream = [IO.File]::OpenRead($path)
            try { $inputStream.CopyTo($output, 1MB) } finally { $inputStream.Dispose() }
        }
        $output.Flush($true)
    } finally { $output.Dispose() }
    if (-not (_Valid-File $assembling $Bundle.size $Bundle.sha256)) {
        throw "Combined archive failed its size/SHA-256 check: $($Bundle.archive)"
    }
    Move-Item -LiteralPath $assembling -Destination $archive -Force
    return $archive
}

function _Install-Native([string]$Archive) {
    if (-not (Test-Path -LiteralPath $TarExecutable -PathType Leaf)) {
        throw 'Windows tar.exe is unavailable. Use an up-to-date Windows 10 or Windows 11 installation.'
    }
    $destination = _Assert-ProjectPath (Join-Path $ProjectRoot 'models\cache')
    if (Test-Path -LiteralPath $destination) {
        if (@(Get-ChildItem -LiteralPath $destination -Force).Count -gt 0) {
            throw 'models\cache contains an incomplete runtime. Move that folder aside, then run start.cmd again.'
        }
        [IO.Directory]::Delete($destination)
    }
    $listing = _Run-Checked $TarExecutable @('-tzf', $Archive) 'Reading the Windows archive'
    foreach ($entry in ($listing -split "`n")) {
        $member = $entry.TrimEnd("`r").Replace('\', '/')
        if ($member -and ($member -cnotmatch '^(\./)?models(/cache(/.*)?|/?)$' -or
            $member -match '(^|/)\.\.(/|$)' -or $member.Contains(':'))) {
            throw "Unexpected Windows archive path: $member"
        }
    }
    $staging = _ProjectDirectory ('models\.native-stage-' + [Guid]::NewGuid().ToString('N'))
    try {
        Write-Host 'Installing the Windows audio runtime...'
        _Run-Checked $TarExecutable @('-xzf', $Archive, '-C', $staging) 'Extracting the Windows runtime' | Out-Null
        if (-not (_Native-Ready $staging)) { throw 'The Windows archive does not contain the complete audio runtime.' }
        # Publish the entire cache in one same-volume rename. Interrupted copies
        # cannot leave half-installed dependencies that prevent the next retry.
        [IO.Directory]::Move((Join-Path $staging 'models\cache'), $destination)
    } finally {
        $verifiedStaging = _Assert-ProjectPath $staging
        if ([IO.Path]::GetFileName($verifiedStaging) -notlike '.native-stage-*') {
            throw 'Refusing to remove an unrecognized staging directory.'
        }
        Remove-Item -LiteralPath $verifiedStaging -Recurse -Force
    }
}

function _Distribution-Record([string]$Name) {
    $registryRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path -LiteralPath $registryRoot)) { return $null }
    $matches = @(Get-ChildItem -LiteralPath $registryRoot | ForEach-Object {
        $record = Get-ItemProperty -LiteralPath $_.PSPath
        if ($record.DistributionName -ceq $Name) { $record }
    })
    if ($matches.Count -gt 1) { throw 'Multiple WSL registrations claim this project name.' }
    if ($matches.Count -eq 1) { return $matches[0] }
    return $null
}

function _Verify-Distribution([string]$Name, [string]$InstallDirectory, [string]$Tag) {
    $record = _Distribution-Record $Name
    if ($null -eq $record) { throw "The dedicated WSL distribution is missing: $Name" }
    $registeredPath = [string]$record.BasePath
    if ($registeredPath.StartsWith('\\?\')) { $registeredPath = $registeredPath.Substring(4) }
    $registeredPath = [IO.Path]::GetFullPath($registeredPath).TrimEnd('\')
    if ($record.Version -ne 2 -or -not $registeredPath.Equals($InstallDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        throw "WSL name $Name is already registered outside this project's WSL2 runtime. It will not be changed."
    }
    $markerCheck = "import pathlib,sys; p=pathlib.Path('/etc/elysia-singing-release'); assert p.is_file() and p.read_text().strip()==sys.argv[1], 'Release identity mismatch'"
    _Run-Checked $WslExecutable @('--distribution', $Name, '--user', 'elysia', '--exec',
        '/usr/bin/python3', '-c', $markerCheck, $Tag) 'Checking the dedicated WSL release' | Out-Null
    $healthCheck = @'
import pathlib,sys
r=pathlib.Path.home()/'.local/share/elysia-ai/soulx'
assert sys.version_info[:2]==(3,10), 'Unsupported bundled Python'
required=('prep-env/bin/python','infer-env/bin/python','source/soulxsinger/models/soulxsinger_svc.py','models/SoulX-SVC/model-svc.pt','models/SoulX-SVC/whisper-base/model.safetensors','models/SoulX-Singer-Preprocess/rmvpe/rmvpe.pt','prompts/singing-v1/elysia.wav','prompts/singing-v1/elysia.npy')
assert all((r/p).is_file() and (r/p).stat().st_size>0 for p in required), 'Bundled singing assets are missing'
import numpy,torch,torchaudio,soundfile,transformers
assert torch.cuda.is_available(), 'NVIDIA CUDA is unavailable; install a current NVIDIA Windows driver and enable WSL2 GPU support'
print('Bundled singing runtime is ready.')
'@
    $message = _Run-Checked $WslExecutable @('--distribution', $Name, '--user', 'elysia', '--exec',
        '/home/elysia/.local/share/elysia-ai/soulx/infer-env/bin/python', '-B', '-c', $healthCheck) 'Checking the singing runtime'
    Write-Host $message
}

function _Write-State([string]$Name, [string]$Tag) {
    $path = _Assert-ProjectPath (Join-Path $ProjectRoot 'models\runtime-state.json')
    $temporary = _Assert-ProjectPath ($path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $record = [ordered]@{schema_version = 1; distribution = $Name; release_tag = $Tag}
    [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    try {
        if (Test-Path -LiteralPath $path) {
            # PowerShell 5.1 otherwise coerces $null to an empty backup path.
            [IO.File]::Replace($temporary, $path, [System.Management.Automation.Language.NullString]::Value)
        }
        else { [IO.File]::Move($temporary, $path) }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function _Clear-DownloadCache($Manifest) {
    # Keep verified parts after interrupted setup, but do not permanently retain
    # a second compressed copy of both large runtimes after successful install.
    $downloadRoot = Join-Path $ProjectRoot 'models\downloads'
    if (-not (Test-Path -LiteralPath $downloadRoot)) { return }
    foreach ($kind in @('windows', 'linux')) {
        $bundle = $Manifest.bundles.$kind
        $names = @($bundle.archive, ($bundle.archive + '.assembling'))
        foreach ($part in $bundle.parts) { $names += @($part.name, ($part.name + '.download')) }
        foreach ($name in $names) {
            $path = _Assert-ProjectPath (Join-Path $downloadRoot $name)
            if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
        }
    }
}

$lock = $null
$locked = $false
try {
    $manifest = _Read-Manifest
    if ($BundleDirectory) { $BundleDirectory = (Resolve-Path -LiteralPath $BundleDirectory).ProviderPath }
    $workspace = _ProjectDirectory 'workspace'
    $lockPath = _Assert-ProjectPath (Join-Path $workspace 'console.lock')
    $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    if ($lock.Length -eq 0) { $lock.WriteByte(0); $lock.Flush() }
    try { $lock.Lock(0, 1); $locked = $true }
    catch { throw 'Another conversion or runtime setup is already running in this project.' }
    if (-not (Test-Path -LiteralPath $WslExecutable -PathType Leaf)) {
        throw 'WSL2 is required. In an Administrator CMD run: wsl --install --no-distribution ; restart Windows if requested, then run start.cmd again.'
    }
    try { _Run-Checked $WslExecutable @('--status') 'Checking WSL2' | Out-Null }
    catch { throw 'WSL2 is not ready. In an Administrator CMD run: wsl --install --no-distribution ; restart Windows if requested, then run start.cmd again. Install a current NVIDIA Windows driver for GPU inference.' }
    $hashProvider = [Security.Cryptography.SHA256]::Create()
    try {
        $rootHash = [BitConverter]::ToString($hashProvider.ComputeHash([Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant()))).Replace('-', '').ToLowerInvariant()
    } finally { $hashProvider.Dispose() }
    $distribution = 'Elysia-Singing-' + $rootHash.Substring(0, 12)
    $installDirectory = _Assert-ProjectPath (Join-Path $ProjectRoot 'models\wsl')
    if (-not (_Native-Ready $ProjectRoot)) {
        if ($CheckOnly) { throw 'The Windows audio runtime is missing or incomplete. Run start.cmd to install it.' }
        $archive = _Get-Archive $manifest.bundles.windows $manifest.release_tag
        _Install-Native $archive
    }
    if ($null -eq (_Distribution-Record $distribution)) {
        if ($CheckOnly) { throw 'The dedicated singing runtime has not been installed. Run start.cmd to install it.' }
        if ((Test-Path -LiteralPath $installDirectory) -and @(Get-ChildItem -LiteralPath $installDirectory -Force).Count -gt 0) {
            throw 'models\wsl contains an unregistered runtime. Move that folder aside before setup; existing data will not be overwritten.'
        }
        $archive = _Get-Archive $manifest.bundles.linux $manifest.release_tag
        [IO.Directory]::CreateDirectory($installDirectory) | Out-Null
        Write-Host 'Importing the dedicated Elysia Singing WSL2 runtime...'
        _Run-Checked $WslExecutable @('--import', $distribution, $installDirectory, $archive, '--version', '2') 'Importing the singing runtime' | Out-Null
    }
    _Verify-Distribution $distribution $installDirectory $manifest.release_tag
    if (-not $CheckOnly) {
        _Write-State $distribution $manifest.release_tag
        _Clear-DownloadCache $manifest
    }
    Write-Host 'Runtime checks passed.'
} catch {
    Write-Host ('Setup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $lock) {
        if ($locked) { $lock.Unlock(0, 1) }
        $lock.Dispose()
    }
}
exit 0
