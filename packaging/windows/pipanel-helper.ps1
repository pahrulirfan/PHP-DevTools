# PIPanel's only administrator step on Windows: send the DNS questions for *.test to PIPanel's own DNS server
# (127.0.0.1:53) with a Name Resolution Policy Table rule. Installed next to PIPanel.exe in Program Files, where
# only administrators can change it. Started elevated (UAC) by PIPanel:
#
#   powershell -ExecutionPolicy Bypass -File pipanel-helper.ps1 -Command setup-dns -Tld test
param(
    [Parameter(Mandatory = $true)][ValidateSet("setup-dns", "remove-dns", "check")][string]$Command,
    [ValidatePattern("^[a-z][a-z0-9-]{0,30}$")][string]$Tld = "test",
    [string]$CaFile = ""
)
$ErrorActionPreference = "Stop"
$namespace = ".$Tld"

function Remove-Ours {
    Get-DnsClientNrptRule | Where-Object { $_.Comment -eq "PIPanel" -and $_.Namespace -contains $namespace } |
        ForEach-Object { Remove-DnsClientNrptRule -Name $_.Name -Force }
}

switch ($Command) {
    "setup-dns" {
        Remove-Ours
        Add-DnsClientNrptRule -Namespace $namespace -NameServers "127.0.0.1" -Comment "PIPanel" | Out-Null
        Clear-DnsClientCache
        Write-Output "PIPanel: $namespace now resolves through 127.0.0.1"
        if ($CaFile) {
            # only mkcert's own root certificate of this user, and nothing with a private key
            $expected = Join-Path $env:LOCALAPPDATA "mkcert\rootCA.pem"
            if ((Resolve-Path $CaFile).Path -ne (Resolve-Path $expected).Path) { Write-Error "only $expected may be trusted"; exit 3 }
            $text = Get-Content -Raw $CaFile
            if ($text -match "PRIVATE KEY" -or ([regex]::Matches($text, "BEGIN CERTIFICATE")).Count -ne 1) { Write-Error "not a single certificate"; exit 3 }
            Import-Certificate -FilePath $CaFile -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
            Write-Output "PIPanel: HTTPS certificates of mkcert are trusted"
        }
    }
    "remove-dns" {
        Remove-Ours
        Clear-DnsClientCache
    }
    "check" {
        $rule = Get-DnsClientNrptRule | Where-Object { $_.Comment -eq "PIPanel" -and $_.Namespace -contains $namespace }
        if (-not $rule) { exit 1 }
    }
}
exit 0
