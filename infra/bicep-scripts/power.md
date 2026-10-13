
```

$ResourceGroup = "rg_gtgh_data_15"
$Location = " germanywestcentral"

# Must be globally unique and lowercase.
$AcrName = "dim02accwse12345"

$EnvironmentName = "assets-env"
$IdentityName = "assets-app-pull"
$ContainerAppName = "assets-app"

$LocalImage = "assets-api:latest"
$ImageName = "assets-api"
$ImageTag = "latest"
$WorkspaceName = "log-assets"




<!-- # Create the resource group if needed.
az group create `
  --name $ResourceGroup `
  --location $Location -->

# First deployment: registry, workspace, and Container Apps environment.
az deployment group create `
  --resource-group $ResourceGroup `
  --template-file infrastructure.bicep `
  --parameters `
    location=$Location `
    acrName=$AcrName `
    environmentName=$EnvironmentName `
    workspaceName=$WorkspaceName

# Build locally if the image is not already built.
docker build -t $LocalImage .

# Push the image to the newly created registry.
#docker desktop must be running
az acr login --name $AcrName

$LoginServer = az acr show `
  --name $AcrName `
  --query loginServer `
  --output tsv

docker tag $LocalImage "${LoginServer}/$LocalImage"
docker push "${LoginServer}/$LocalImage"

# Second deployment: identity, AcrPull assignment, and Container App.
$ApiKey = Read-Host "Enter INCIDENTS_API_KEY"

az deployment group create `
  --resource-group $ResourceGroup `
  --template-file app.bicep `
  --parameters `
    location=$Location `
    acrName=$AcrName `
    environmentName=$EnvironmentName `
    containerAppName=$ContainerAppName `
    imageName=$LocalImage `
    incidentsApiKey=$ApiKey `
  --query "properties.outputs.url.value" `
  --output tsv