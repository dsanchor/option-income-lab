targetScope = 'resourceGroup'

param config object

@secure()
param telegramBotToken string = ''

@secure()
param telegramChatId string = ''

@secure()
param entraClientSecret string = ''

param frontendExternal bool = false

param preservedLifecycleRules array = []

module observability 'observability.bicep' = {
  name: 'observability'
  params: {
    location: config.location
    workspaceName: config.names.logAnalytics
    tags: config.tags
  }
}

module environment 'container-apps-environment.bicep' = {
  name: 'container-apps-environment'
  params: {
    location: config.location
    environmentName: config.names.containerAppsEnvironment
    workspaceId: observability.outputs.workspaceId
    workspaceKey: listKeys(resourceId('Microsoft.OperationalInsights/workspaces', config.names.logAnalytics), '2023-09-01').primarySharedKey
    tags: config.tags
  }
  dependsOn: [
    observability
  ]
}

module cosmos 'cosmos.bicep' = {
  name: 'cosmos'
  params: {
    location: config.location
    accountName: config.names.cosmosAccount
    databaseName: config.cosmos.databaseName
    containers: config.cosmos.containers
    tags: config.tags
  }
}

module foundry 'foundry.bicep' = {
  name: 'foundry'
  params: {
    location: config.location
    accountName: config.names.foundryAccount
    projectName: config.names.foundryProject
    deployments: config.models.deployments
    tags: config.tags
  }
}

module backupIdentity 'identities-rbac.bicep' = {
  name: 'backup-identity-rbac'
  params: {
    location: config.location
    identityName: config.names.backupIdentity
    tags: config.tags
  }
}

module apps 'apps.bicep' = {
  name: 'container-apps'
  params: {
    location: config.location
    environmentId: environment.outputs.environmentId
    apiName: config.names.apiApp
    frontendName: config.names.frontendApp
    mcpName: config.names.mcpApp
    apiImage: config.images.api
    frontendImage: config.images.frontend
    apiConfig: config.apps.api
    frontendConfig: config.apps.frontend
    mcpConfig: config.apps.mcp
    environmentDefaultDomain: environment.outputs.defaultDomain
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosKey: listKeys(resourceId('Microsoft.DocumentDB/databaseAccounts', config.names.cosmosAccount), '2024-05-15').primaryMasterKey
    foundryEndpoint: foundry.outputs.endpoint
    foundryKey: listKeys(resourceId('Microsoft.CognitiveServices/accounts', config.names.foundryAccount), '2025-06-01').key1
    defaultModelDeployment: config.models.routing.default
    telegramBotToken: telegramBotToken
    telegramChatId: telegramChatId
    entraClientSecret: entraClientSecret
    frontendExternal: frontendExternal
    tags: config.tags
  }
  dependsOn: [
    environment
    cosmos
    foundry
  ]
}

module backup 'backup.bicep' = {
  name: 'backup'
  params: {
    location: config.location
    environmentId: environment.outputs.environmentId
    storageAccountName: config.names.backupStorage
    containerName: config.names.backupContainer
    identityId: backupIdentity.outputs.identityId
    identityPrincipalId: backupIdentity.outputs.identityPrincipalId
    identityClientId: backupIdentity.outputs.identityClientId
    tagWriterRoleDefinitionId: backupIdentity.outputs.tagWriterRoleDefinitionId
    jobName: config.names.backupJob
    image: config.images.api
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosKey: listKeys(resourceId('Microsoft.DocumentDB/databaseAccounts', config.names.cosmosAccount), '2024-05-15').primaryMasterKey
    backupConfig: config.backup
    preservedLifecycleRules: preservedLifecycleRules
    tags: config.tags
  }
  dependsOn: [
    environment
    cosmos
    backupIdentity
  ]
}

module diagnostics 'diagnostics.bicep' = {
  name: 'diagnostics'
  params: {
    workspaceId: observability.outputs.workspaceId
    cosmosAccountName: config.names.cosmosAccount
    foundryAccountName: config.names.foundryAccount
    storageAccountName: config.names.backupStorage
    containerAppsEnvironmentName: config.names.containerAppsEnvironment
    apiAppName: config.names.apiApp
    frontendAppName: config.names.frontendApp
    mcpAppName: config.names.mcpApp
    backupJobName: config.names.backupJob
    enabled: config.diagnostics.enabled
  }
  dependsOn: [
    cosmos
    foundry
    backup
  ]
}

output apiFqdn string = apps.outputs.apiFqdn
output frontendFqdn string = apps.outputs.frontendFqdn
output mcpFqdn string = apps.outputs.mcpFqdn
output foundryEndpoint string = foundry.outputs.endpoint
output backupIdentityClientId string = backupIdentity.outputs.identityClientId
