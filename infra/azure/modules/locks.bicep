targetScope = 'resourceGroup'

param cosmosAccountName string
param foundryAccountName string
param storageAccountName string

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource cosmosLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'protect-cosmos'
  scope: cosmos
  properties: {
    level: 'CanNotDelete'
    notes: 'Managed by the Option Income Lab provisioner.'
  }
}

resource foundryLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'protect-foundry'
  scope: foundry
  properties: {
    level: 'CanNotDelete'
    notes: 'Managed by the Option Income Lab provisioner.'
  }
}

resource storageLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'protect-backup-storage'
  scope: storage
  properties: {
    level: 'CanNotDelete'
    notes: 'Managed by the Option Income Lab provisioner.'
  }
}
