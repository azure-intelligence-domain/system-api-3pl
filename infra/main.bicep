// 3PL System API: a Python Function on Flex Consumption, in rg-intplat-system-api-3pl. It reads and
// writes the simulated 3PL (Cosmos DB in backend-systems) as its runtime identity, publishes
// logistics facts to evgt-intplat-logistics, and only API Management's people gateway can call it.

param location string = 'belgiumcentral'

@description('Application (client) id of the intplat-platform-apis Entra application (ADR 0014).')
param platformApisAppId string = 'e3337087-144a-4b2e-8faa-c802660b9dee'

var tags = {
  project: 'ai-intelligence-domain'
  component: 'system-api-3pl'
  owner: 'logistics-team'
}

resource runtime 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-intplat-system-api-3pl-runtime'
  scope: resourceGroup('rg-intplat-identities')
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: 'appi-intplat-demo'
  scope: resourceGroup('rg-intplat-platform')
}

// System APIs are served by the people gateway only (product system-apis).
resource gateway 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: 'apim-intplat-demo'
  scope: resourceGroup('rg-intplat-platform')
}

resource topic 'Microsoft.EventGrid/topics@2025-02-15' existing = {
  name: 'evgt-intplat-logistics'
  scope: resourceGroup('rg-intplat-platform')
}

module function 'modules/function-app.bicep' = {
  name: 'function'
  params: {
    name: 'func-intplat-system-api-3pl'
    location: location
    tags: tags
    runtimeIdentityId: runtime.id
    runtimeClientId: runtime.properties.clientId
    runtimePrincipalId: runtime.properties.principalId
    appInsightsConnectionString: appInsights.properties.ConnectionString
    platformApisAppId: platformApisAppId
    allowedPrincipalIds: [gateway.identity.principalId]
    appSettings: {
      SYSTEM_ENDPOINT: 'https://cosmos-intplat-3pl.documents.azure.com:443/'
      SYSTEM_DATABASE: '3pl'
      TOPIC_ENDPOINT: topic.properties.endpoint
    }
  }
}

output functionName string = function.outputs.name
output hostName string = function.outputs.defaultHostName
