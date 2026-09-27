targetScope = 'subscription'

@description('Validated, non-secret provisioner configuration.')
param config object

@secure()
@description('Optional Telegram bot token. Empty disables Telegram runtime configuration.')
param telegramBotToken string = ''

@secure()
@description('Optional Telegram chat ID. Empty disables Telegram runtime configuration.')
param telegramChatId string = ''

@secure()
@description('Microsoft Entra application client secret, supplied only through stdin at apply time.')
param entraClientSecret string = ''

@description('Expose the frontend only after Easy Auth has been deployed and verified.')
param frontendExternal bool = false

@description('Existing lifecycle rules not owned by this provisioner.')
param preservedLifecycleRules array = []

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: config.resourceGroupName
  location: config.location
  tags: config.tags
}

module stack 'modules/stack.bicep' = {
  name: 'option-income-stack'
  scope: resourceGroup
  params: {
    config: config
    telegramBotToken: telegramBotToken
    telegramChatId: telegramChatId
    entraClientSecret: entraClientSecret
    frontendExternal: frontendExternal
    preservedLifecycleRules: preservedLifecycleRules
  }
}

output resourceGroupId string = resourceGroup.id
output apiFqdn string = stack.outputs.apiFqdn
output frontendFqdn string = stack.outputs.frontendFqdn
output foundryEndpoint string = stack.outputs.foundryEndpoint
output backupIdentityClientId string = stack.outputs.backupIdentityClientId
output routing object = config.models.routing
