param(
    [Parameter(Mandatory)]
    [string]$ResourceGroup,

    [Parameter(Mandatory)]
    [string]$Location,

    [Parameter(Mandatory)]
    [string]$AcrName,

    [Parameter(Mandatory)]
    [string]$EnvironmentName,

    [Parameter(Mandatory)]
    [string]$ContainerAppName
)

$SourceImage = "assets-api:latest"   # already built locally (docker images)
$LocalImage = "assets-api:latest"  # name:tag pushed to ACR and run by the app
$RepoRoot = Resolve-Path "$PSScriptRoot\..\.."

# App settings come from the repo .env (same names the app reads); nothing is typed or committed
$Settings = @{}
Get-Content "$RepoRoot\.env" | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*"?(.*?)"?\s*$') { $Settings[$Matches[1]] = $Matches[2] }
}

function Assert-LastCommand {
    if ($LASTEXITCODE -ne 0) {
        throw "The previous command failed with exit code $LASTEXITCODE"
    }
}

az deployment group create `
    --resource-group $ResourceGroup `
    --template-file "$PSScriptRoot\infrastructure.bicep" `
    --parameters `
        location=$Location `
        acrName=$AcrName
Assert-LastCommand

docker image inspect $SourceImage --format "using {{.Id}}"
Assert-LastCommand

az acr login --name $AcrName
Assert-LastCommand

$LoginServer = az acr show `
    --name $AcrName `
    --query loginServer `
    --output tsv
Assert-LastCommand

docker tag $SourceImage "${LoginServer}/$LocalImage"
Assert-LastCommand

docker push "${LoginServer}/$LocalImage"
Assert-LastCommand

# Secrets go through a temp parameters file: `$` and `;` in AUTH_USERS break az.cmd on the command line
$ParamsFile = Join-Path $env:TEMP "pm-api-params.json"
try {
    $values = @{
        location                  = $Location
        acrName                   = $AcrName
        environmentName           = $EnvironmentName
        containerAppName          = $ContainerAppName
        imageName                 = $LocalImage
        jwtSecret                 = $Settings.JWT_SECRET
        authUsers                 = $Settings.AUTH_USERS
        databricksToken           = $Settings.DATABRICKS_TOKEN
        azureOpenaiApiKey         = $Settings.AZURE_OPENAI_API_KEY
        redisUrl                  = $Settings.REDIS_URL
        databricksHost            = $Settings.DATABRICKS_HOST
        databricksHttpPath        = $Settings.DATABRICKS_HTTP_PATH
        azureOpenaiEndpoint       = $Settings.AZURE_OPENAI_ENDPOINT
        azureOpenaiDeploymentName = $Settings.AZURE_OPENAI_DEPLOYMENT_NAME
        openaiApiVersion          = $Settings.OPENAI_API_VERSION
        goldCatalog               = $Settings.GOLD_CATALOG
        goldSchema                = $Settings.GOLD_SCHEMA
        goldTablePrefix           = $Settings.GOLD_TABLE_PREFIX
    }
    $parameters = @{}
    foreach ($k in $values.Keys) { if ($values[$k]) { $parameters[$k] = @{ value = $values[$k] } } }
    @{
        '$schema'      = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"
        contentVersion = "1.0.0.0"
        parameters     = $parameters
    } | ConvertTo-Json -Depth 5 | Set-Content $ParamsFile -Encoding utf8

    az deployment group create `
        --resource-group $ResourceGroup `
        --template-file "$PSScriptRoot\app.bicep" `
        --parameters "@$ParamsFile" `
        --query "properties.outputs.url.value" `
        --output tsv
    Assert-LastCommand
}
finally {
    Remove-Item $ParamsFile -ErrorAction SilentlyContinue
}