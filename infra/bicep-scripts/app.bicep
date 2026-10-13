param location string = resourceGroup().location
param acrName string
param environmentName string
param containerAppName string

// Repository and tag, for example: assets-api:0.1
param imageName string

// Secrets of the app (names as in .env); stored as Container App secrets, never in the image
@secure()
param jwtSecret string // at least 32 characters, signs the session cookie

@secure()
param authUsers string // "email=scrypt$salt$hash;email2=..."

@secure()
param databricksToken string

@secure()
param azureOpenaiApiKey string

@secure()
param redisUrl string // internal address of the existing Redis app, same environment: redis://:<password>@redis:<port>/0

// Plain settings
param databricksHost string
param databricksHttpPath string
param azureOpenaiEndpoint string
param azureOpenaiDeploymentName string
param openaiApiVersion string
param goldCatalog string = 'data2026acc123456'
param goldSchema string = 'team3'
param goldTablePrefix string = 'test_'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

// Image pull uses the ACR admin user (infrastructure.bicep enables it): the pipeline identity cannot create
// role assignments, so a managed identity with AcrPull is not possible. Switch back once an Owner can grant AcrPull.
resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: containerAppName
  location: location

  properties: {
    managedEnvironmentId: environment.id

    configuration: {
      activeRevisionsMode: 'Single'

      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }

      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'registry-password'
        }
      ]

      secrets: [
        { name: 'registry-password', value: acr.listCredentials().passwords[0].value }
        { name: 'jwt-secret', value: jwtSecret }
        { name: 'auth-users', value: authUsers }
        { name: 'databricks-token', value: databricksToken }
        { name: 'azure-openai-api-key', value: azureOpenaiApiKey }
        { name: 'redis-url', value: redisUrl }
      ]
    }

    template: {
      containers: [
        {
          name: containerAppName
          image: '${acr.properties.loginServer}/${imageName}'

          env: [
            { name: 'ENVIRONMENT', value: 'prod' } // the app refuses to start without secure auth
            { name: 'COOKIE_SECURE', value: 'true' }
            { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
            { name: 'AUTH_USERS', secretRef: 'auth-users' }
            { name: 'DATABRICKS_HOST', value: databricksHost }
            { name: 'DATABRICKS_HTTP_PATH', value: databricksHttpPath }
            { name: 'DATABRICKS_TOKEN', secretRef: 'databricks-token' }
            { name: 'GOLD_CATALOG', value: goldCatalog }
            { name: 'GOLD_SCHEMA', value: goldSchema }
            { name: 'GOLD_TABLE_PREFIX', value: goldTablePrefix }
            { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenaiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-api-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT_NAME', value: azureOpenaiDeploymentName }
            { name: 'OPENAI_API_VERSION', value: openaiApiVersion }
            { name: 'REDIS_URL', secretRef: 'redis-url' }
          ]

          // /health = process is up, /ready = Gold (Databricks) reachable
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
            }
            {
              type: 'Startup'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 5
              failureThreshold: 12
            }
          ]

          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]

      scale: {
        // exactly one replica: pending assistant plans live in memory, scale-to-zero or a 2nd replica loses/splits them
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'

