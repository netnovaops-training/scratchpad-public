<#
    Install Rust on Windows, for editing the apps locally.

    -------------------------------------------------------------------------
    YOU DO NOT NEED THIS TO RUN ANYTHING.

    The containers build themselves: `rust\Dockerfile` carries its own compiler,
    so `websites.cmd` works on a machine with no Rust installed at all. Nothing
    on this page is required for the sites to be live.

    What it buys you is the edit loop. Building through Docker means a rebuild
    and a container restart for every change -- twenty to sixty seconds. With
    Rust on the host it's `cargo run` and about two seconds, with the compiler
    errors printed straight into your terminal.

    So: install this when you want to CHANGE the sites. Skip it if you just
    want them running.
    -------------------------------------------------------------------------

    Double-click rust.cmd.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

function Have { param($cmd) return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Write-Host ""
Write-Host "  Rust on Windows -- for editing the sites" -ForegroundColor Cyan
Write-Host "  ---------------------------------------" -ForegroundColor DarkGray
Write-Host "  (the containers build without this - see the top of rust-setup.ps1)" -ForegroundColor DarkGray

# --- the C linker ------------------------------------------------------------
# The single most common way a first Rust install on Windows fails. rustc
# compiles Rust; it hands the final link to Microsoft's linker, which arrives
# with the Visual Studio Build Tools. Without it the very first `cargo build`
# stops with "linker `link.exe` not found", which reads like a Rust problem and
# is not one.
Head "The C linker (this is the part that catches people)"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$haveMsvc = $false
if (Test-Path $vswhere) {
    $found = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    if ($found) { $haveMsvc = $true; Ok "Visual Studio C++ build tools found" "$found" }
}
if (-not $haveMsvc -and (Have "link.exe")) { $haveMsvc = $true; Ok "a Microsoft linker is on PATH" }

if (-not $haveMsvc) {
    Warn "the MSVC build tools aren't installed" "Rust will install but the first build will fail at the link step"
    Write-Host ""
    Write-Host "  This is a real download -- roughly 2-4 GB. Two ways:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    winget install --id Microsoft.VisualStudio.2022.BuildTools --override `"--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended`"" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  or download 'Build Tools for Visual Studio' from Microsoft and" -ForegroundColor Yellow
    Write-Host "  tick 'Desktop development with C++'." -ForegroundColor Yellow
    Write-Host ""
    $go = Read-Host "  Install it now with winget? (y/N)"
    if ($go -eq "y") {
        if (Have "winget") {
            winget install --id Microsoft.VisualStudio.2022.BuildTools --accept-source-agreements --accept-package-agreements `
                --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
            Ok "build tools installed" "a reboot is sometimes needed before the linker is on PATH"
        } else {
            Bad "winget isn't available" "install 'App Installer' from the Microsoft Store, or download the build tools by hand"
        }
    } else {
        Warn "skipped" "you can re-run this script after installing them"
    }
}

# --- rustup ------------------------------------------------------------------
Head "Rust"
if (Have "cargo") {
    $v = (cargo --version) 2>$null
    Ok "Rust is already installed" $v
    if (Have "rustup") {
        Write-Host "         updating..." -ForegroundColor DarkGray
        rustup update stable 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    }
} else {
    if (Have "winget") {
        Write-Host "  Installing rustup via winget..." -ForegroundColor DarkGray
        winget install --id Rustlang.Rustup --accept-source-agreements --accept-package-agreements
    } else {
        Warn "winget isn't available" "downloading the official installer instead"
        $exe = "$env:TEMP\rustup-init.exe"
        try {
            Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $exe -UseBasicParsing -ErrorAction Stop
            & $exe -y --default-toolchain stable
        } catch {
            Bad "couldn't download rustup" "get it from https://rustup.rs and re-run this"
        }
    }

    # winget updates PATH for NEW shells, not this one. Patch it in so the
    # verification below can actually run.
    $cargoBin = "$env:USERPROFILE\.cargo\bin"
    if (Test-Path $cargoBin) { $env:PATH = "$cargoBin;$env:PATH" }

    if (Have "cargo") { Ok "Rust installed" (cargo --version) }
    else { Bad "cargo still isn't on PATH" "close this window, open a new one, and re-run rust.cmd" }
}

# rust-analyzer is what makes an editor useful for Rust: types on hover, jump
# to definition, errors as you type. It is not optional in practice.
if (Have "rustup") {
    rustup component add rust-analyzer rustfmt clippy 2>&1 | Out-Null
    Ok "rust-analyzer, rustfmt and clippy added" "VS Code: install the 'rust-analyzer' extension too"
}

# --- does the workspace actually build ---------------------------------------
Head "Building the workspace"
if (-not (Have "cargo")) {
    Bad "skipped - cargo isn't available in this window yet" "open a new terminal and run:  cargo build --release  in ai-stack\rust"
} else {
    Push-Location "$PSScriptRoot\rust"
    Write-Host "  First build downloads ~200 crates and takes a few minutes." -ForegroundColor DarkGray
    Write-Host "  Every build after this one takes seconds." -ForegroundColor DarkGray
    Write-Host ""
    cargo build --release
    if ($LASTEXITCODE -eq 0) { Ok "the workspace builds" }
    else { Bad "the build failed" "if it says 'linker link.exe not found', that's the MSVC step above" }
    Pop-Location
}

# --- how to use it -----------------------------------------------------------
Head "The edit loop"
$loop = @(
    "  Run the landing page locally, without Docker:",
    "",
    "      cd `"$PSScriptRoot\rust`"",
    "      cargo run -p landing",
    "",
    "  Then open  http://localhost:8010",
    "",
    "  Edit rust\landing\templates\index.html or src\main.rs, Ctrl-C, run again.",
    "  Two seconds, not a container rebuild.",
    "",
    "  Instant reload on save (worth the one-off install):",
    "",
    "      cargo install cargo-watch",
    "      cargo watch -x `"run -p landing`"",
    "",
    "  Before you commit anything:",
    "",
    "      cargo fmt          format it",
    "      cargo clippy       catch the things that compile but are wrong",
    "",
    "  When it's right, put it in the container:",
    "",
    "      cd `"$PSScriptRoot`"",
    "      docker compose -f docker-compose.yml -f docker-compose.web.yml build landing",
    "      docker compose -f docker-compose.yml -f docker-compose.web.yml --profile web up -d landing"
)
foreach ($l in $loop) { Write-Host $l -ForegroundColor DarkGray }

Write-Host ""
Read-Host "  Press Enter to close"
