param location string = resourceGroup().location
param acrName string

// Only the registry: the web app runs in the existing Container Apps environment of Redis
// (redis-assets2, logs included), see app.bicep.
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true // the Container App pulls with it, see app.bicep
  }
}

output loginServer string = acr.properties.loginServer