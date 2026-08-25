# open-jsx.ps1 - Renders a .jsx file in the default browser.
# Run with no argument to install it as the .jsx handler.
# ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI,
# so non-ASCII literals here would be corrupted.
param(
    [string]$JsxPath
)

$ErrorActionPreference = 'Stop'

# ---- Install mode: register this file as the .jsx handler.
if ([string]::IsNullOrWhiteSpace($JsxPath)) {
    $self = $PSCommandPath
    $progId = 'HKCU:\Software\Classes\JsxViewer.File'
    $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$self`" `"%1`""

    New-Item -Path "$progId\shell\open\command" -Force | Out-Null
    Set-ItemProperty -Path $progId -Name '(default)' -Value 'JSX Browser Viewer'
    Set-ItemProperty -Path "$progId\shell\open\command" -Name '(default)' -Value $command
    New-Item -Path 'HKCU:\Software\Classes\.jsx' -Force | Out-Null
    Set-ItemProperty -Path 'HKCU:\Software\Classes\.jsx' -Name '(default)' -Value 'JsxViewer.File'

    Write-Host ''
    Write-Host 'Installed. Double-click any .jsx file to open it in your browser.' -ForegroundColor Green
    Write-Host "  handler  : $self"
    Write-Host '  registry : HKCU\Software\Classes\.jsx  (current user only)'
    Write-Host '  uninstall: Remove-Item HKCU:\Software\Classes\.jsx -Recurse'
    Write-Host ''
    exit 0
}

try {
    if (-not (Test-Path -LiteralPath $JsxPath)) {
        throw "File not found: $JsxPath"
    }

    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($JsxPath)
    $src = Get-Content -LiteralPath $JsxPath -Raw -Encoding UTF8

    # Drop imports - React comes from the UMD global instead.
    $src = [regex]::Replace($src, '(?m)^\s*import\b[^\r\n]*\r?\n', '')

    # Find the root component declared with "export default".
    $componentName = $null
    $m = [regex]::Match($src, '(?m)^\s*export\s+default\s+function\s+([A-Za-z_$][\w$]*)')
    if ($m.Success) {
        $componentName = $m.Groups[1].Value
        $src = [regex]::Replace($src, '(?m)^(\s*)export\s+default\s+function\s+', '$1function ')
    }
    else {
        $m = [regex]::Match($src, '(?m)^\s*export\s+default\s+([A-Za-z_$][\w$]*)\s*;?\s*$')
        if ($m.Success) {
            $componentName = $m.Groups[1].Value
            $src = [regex]::Replace($src, '(?m)^\s*export\s+default\s+[A-Za-z_$][\w$]*\s*;?\s*$', '')
        }
    }
    if (-not $componentName) { $componentName = 'App' }

    # Drop named exports; keep a literal </script> from closing our block.
    $src = [regex]::Replace($src, '(?m)^(\s*)export\s+', '$1')
    $src = $src -replace '</script>', '<\/script>'

    # Babel pinned to 7: the react preset in 8+ defaults to the automatic
    # runtime, which injects an import and breaks the whole render.
    $template = @'
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone@7/babel.min.js"></script>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { margin: 0; background: #f3f4f6; }
  #error { display: none; margin: 16px; padding: 16px; border-radius: 8px;
    background: #fef2f2; color: #b91c1c; font-family: Consolas, monospace;
    font-size: 13px; white-space: pre-wrap; }
</style>
</head>
<body>
<div id="root"></div>
<div id="error"></div>
<script>
  window.addEventListener('error', function (e) {
    var box = document.getElementById('error');
    box.style.display = 'block';
    box.textContent = 'Render error: ' + e.message +
      '\n(If a CDN failed to load, check your internet connection.)';
  });
</script>
<script type="text/babel" data-presets="react">
const { useState, useEffect, useMemo, useCallback, useRef, useReducer, useContext, Fragment } = React;

__SOURCE__

ReactDOM.createRoot(document.getElementById('root')).render(<__COMPONENT__ />);
</script>
</body>
</html>
'@

    $html = $template.Replace('__TITLE__', $baseName).Replace('__COMPONENT__', $componentName).Replace('__SOURCE__', $src)

    $outDir = Join-Path $env:TEMP 'jsx-viewer'
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $outPath = Join-Path $outDir ($baseName + '.html')
    [System.IO.File]::WriteAllText($outPath, $html, (New-Object System.Text.UTF8Encoding($false)))

    Start-Process $outPath
}
catch {
    # The window is hidden, so surface failures in a message box.
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message, 'JSX Viewer error',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    exit 1
}
