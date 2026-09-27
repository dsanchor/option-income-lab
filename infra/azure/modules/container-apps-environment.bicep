param location string
param environmentName string
param workspaceId string

@secure()
param workspaceKey string

param tags object

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(workspaceId, '2023-09-01').customerId
        sharedKey: workspaceKey
      }
    }
  }
}

output environmentId string = environment.id
output defaultDomain string = environment.properties.defaultDomain
