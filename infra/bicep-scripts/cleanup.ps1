param(
    [Parameter(Mandatory)]
    [string]$ResourceGroup
)

function Assert-Success {
    if ($LASTEXITCODE -ne 0) {
        throw "Deletion failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Deleting Container App..."
az containerapp delete `
    --name assets-app `
    --resource-group $ResourceGroup `
    --yes
Assert-Success

Write-Host "Deleting Container Apps environment..."
az containerapp env delete `
    --name assets-env `
    --resource-group $ResourceGroup `
    --yes
Assert-Success

Write-Host "Deleting managed identity..."
az identity delete `
    --name assets-app-pull `
    --resource-group $ResourceGroup
Assert-Success

Write-Host "Deleting container registry..."
az acr delete `
    --name dim02accwse12345 `
    --resource-group $ResourceGroup `
    --yes
Assert-Success

Write-Host "Deleting Log Analytics workspace..."
az monitor log-analytics workspace delete `
    --workspace-name log-assets `
    --resource-group $ResourceGroup `
    --yes
Assert-Success

Write-Host "Cleanup complete. Remaining resources:"
az resource list `
    --resource-group $ResourceGroup `
    --output table

