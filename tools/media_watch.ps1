# Streams the Windows "now playing" session (GlobalSystemMediaTransportControls) as JSON lines.
# Started by deckdash.sources.media; one line per second, album art only when it changes.
# Commands (toggle / next / prev) arrive as the content of state\media-cmd.txt.
# ASCII only in this file (PowerShell 5.1 reads BOM-less scripts as ANSI).

param([double]$Interval = 1.0)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out = [Console]::Out

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

function Emit($obj) {
    $out.WriteLine((ConvertTo-Json $obj -Compress -Depth 3))
    $out.Flush()
}

$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
$propsType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$streamType = [Windows.Storage.Streams.IRandomAccessStreamWithContentType]

$cmdFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'state\media-cmd.txt'
$mgr = Await ($mgrType::RequestAsync()) ($mgrType)
$lastArtKey = ''
Emit ([ordered]@{ status = 'Ready' })

while ($true) {
    try {
        $session = $mgr.GetCurrentSession()
        if ($null -eq $session) {
            $lastArtKey = ''
            Emit ([ordered]@{ status = 'None' })
        } else {
            if (Test-Path $cmdFile) {
                $cmd = (Get-Content $cmdFile -Raw).Trim()
                Remove-Item $cmdFile -Force -ErrorAction SilentlyContinue
                switch ($cmd) {
                    'toggle' { $null = Await ($session.TryTogglePlayPauseAsync()) ([bool]) }
                    'next'   { $null = Await ($session.TrySkipNextAsync()) ([bool]) }
                    'prev'   { $null = Await ($session.TrySkipPreviousAsync()) ([bool]) }
                }
            }
            $props = Await ($session.TryGetMediaPropertiesAsync()) ($propsType)
            $info = $session.GetPlaybackInfo()
            $tl = $session.GetTimelineProperties()
            $artKey = [string]$props.Title + '|' + [string]$props.Artist + '|' + [string]$props.AlbumTitle
            $art = $null
            if ($artKey -ne $lastArtKey) {
                $lastArtKey = $artKey
                if ($null -ne $props.Thumbnail) {
                    try {
                        $stream = Await ($props.Thumbnail.OpenReadAsync()) ($streamType)
                        $size = [uint32]$stream.Size
                        if ($size -gt 0 -and $size -lt 4000000) {
                            $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
                            $null = Await ($reader.LoadAsync($size)) ([uint32])
                            $bytes = New-Object byte[] $size
                            $reader.ReadBytes($bytes)
                            $art = [Convert]::ToBase64String($bytes)
                            $reader.Dispose()
                        }
                        $stream.Dispose()
                    } catch {
                        $art = $null
                    }
                }
            }
            Emit ([ordered]@{
                status = [string]$info.PlaybackStatus
                title = [string]$props.Title
                artist = [string]$props.Artist
                album = [string]$props.AlbumTitle
                app = [string]$session.SourceAppUserModelId
                pos = [double]$tl.Position.TotalSeconds
                dur = [double]$tl.EndTime.TotalSeconds
                updated = [double]$tl.LastUpdatedTime.ToUnixTimeMilliseconds() / 1000.0
                art_key = $artKey
                art = $art
            })
        }
    } catch {
        Emit ([ordered]@{ status = 'Error'; error = [string]$_ })
    }
    Start-Sleep -Milliseconds ([int]($Interval * 1000))
}
