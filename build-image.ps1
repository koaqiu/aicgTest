<#
.SYNOPSIS
Build container image with auto-detected engine (Podman or Docker).

.DESCRIPTION
This script auto-detects a container engine and runs a build command.
It can optionally apply China mirror build args for APT and PIP.

.PARAMETER Engine
Container engine: auto, podman, docker. Default is auto.

.PARAMETER UseMirror
When set, use mirror build args for better connectivity in China.

.PARAMETER ImageName
Target image name and tag.

.PARAMETER DockerfilePath
Path to Dockerfile.

.PARAMETER ContextPath
Build context path.

.PARAMETER h
Show help and exit.

.PARAMETER v
Show script version and exit.

.EXAMPLE
.\build-image.ps1

.EXAMPLE
.\build-image.ps1 -UseMirror

.EXAMPLE
.\build-image.ps1 -Engine docker -ImageName aicgtest:web-opt

.NOTES
Version: 1.0.0
#>

[CmdletBinding()]
param(
	[ValidateSet('auto', 'podman', 'docker')]
	[string]$Engine = 'auto',

	[switch]$UseMirror,

	[string]$ImageName = 'aicgtest:web-opt',

	[string]$DockerfilePath = 'Dockerfile',

	[string]$ContextPath = '.',

	[Alias('h')]
	[switch]$Help,

	[Alias('v')]
	[switch]$Version
)

$ScriptVersion = '1.0.0'

if ($Help) {
	Get-Help -Detailed $PSCommandPath
	exit 0
}

if ($Version) {
	Write-Output "build-image.ps1 $ScriptVersion"
	exit 0
}

function Test-Engine {
	param([Parameter(Mandatory = $true)][string]$Name)
	return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Resolve-Engine {
	param([string]$Requested)

	if ($Requested -ne 'auto') {
		if (-not (Test-Engine -Name $Requested)) {
			throw "Requested engine '$Requested' is not available in PATH."
		}
		return $Requested
	}

	if (Test-Engine -Name 'podman') { return 'podman' }
	if (Test-Engine -Name 'docker') { return 'docker' }

	throw 'No supported container engine found. Install podman or docker, or specify -Engine explicitly.'
}

$SelectedEngine = Resolve-Engine -Requested $Engine

$Args = @('build')
if ($SelectedEngine -eq 'podman') {
	$Args += @('--format', 'docker')
}

$Args += @('-f', $DockerfilePath, '-t', $ImageName)

if ($UseMirror) {
	$Args += @(
		'--build-arg', 'APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian',
		'--build-arg', 'APT_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security',
		'--build-arg', 'PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple',
		'--build-arg', 'PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn'
	)
}

$Args += $ContextPath

Write-Output "Engine: $SelectedEngine"
Write-Output "Image : $ImageName"
Write-Output "Mirror: $($UseMirror.IsPresent)"
Write-Output "Run   : $SelectedEngine $($Args -join ' ')"

& $SelectedEngine @Args
$ExitCode = $LASTEXITCODE

if ($ExitCode -ne 0) {
	throw "Build failed with exit code $ExitCode."
}

Write-Output 'Build completed successfully.'