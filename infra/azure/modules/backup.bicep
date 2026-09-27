param location string
param environmentId string
param storageAccountName string
param containerName string
param identityId string
param identityPrincipalId string
param identityClientId string
param tagWriterRoleDefinitionId string
param jobName string
param image string
param cosmosEndpoint string

@secure()
param cosmosKey string

param backupConfig object
param preservedLifecycleRules array = []
param tags object

var blobContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: {
      enabled: true
      days: backupConfig.softDeleteDays
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: backupConfig.softDeleteDays
    }
  }
}

resource backupContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: concat(preservedLifecycleRules, [
        {
          enabled: true
          name: 'backup-staging-1-day'
          type: 'Lifecycle'
          definition: {
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: 1
                }
              }
            }
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/v1/staging/']
            }
          }
        }
        {
          enabled: true
          name: 'backup-daily-retention'
          type: 'Lifecycle'
          definition: {
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: backupConfig.dailyRetentionDays
                }
              }
            }
            filters: {
              blobIndexMatch: [
                {
                  name: 'retentionClass'
                  op: '=='
                  value: 'daily'
                }
              ]
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/v1/daily/']
            }
          }
        }
        {
          enabled: true
          name: 'backup-monthly-anchor-retention'
          type: 'Lifecycle'
          definition: {
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: backupConfig.monthlyAnchorRetentionDays
                }
              }
            }
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/v1/monthly/']
            }
          }
        }
        {
          enabled: true
          name: 'backup-run-retention'
          type: 'Lifecycle'
          definition: {
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: backupConfig.runRetentionDays
                }
              }
            }
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/v1/runs/']
            }
          }
        }
        {
          enabled: true
          name: 'backup-version-retention'
          type: 'Lifecycle'
          definition: {
            actions: {
              version: {
                delete: {
                  daysAfterCreationGreaterThan: backupConfig.softDeleteDays
                }
              }
            }
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/v1/']
            }
          }
        }
      ])
    }
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backupContainer.id, identityPrincipalId, blobContributorRoleId)
  scope: backupContainer
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: blobContributorRoleId
  }
}

resource tagWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backupContainer.id, identityPrincipalId, tagWriterRoleDefinitionId)
  scope: backupContainer
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tagWriterRoleDefinitionId
  }
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 2
      scheduleTriggerConfig: {
        cronExpression: backupConfig.schedule
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: [
        {
          name: 'cosmosdb-key'
          value: cosmosKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'backup'
          image: image
          command: ['python']
          args: ['scripts/run_automatic_backup.py', 'scheduled']
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'AZURE_STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'BACKUP_ENABLED', value: 'true' }
            { name: 'BACKUP_BLOB_CONTAINER', value: containerName }
            { name: 'BACKUP_TIMEZONE', value: backupConfig.timezone }
            { name: 'BACKUP_LOCAL_TIME', value: backupConfig.localTime }
            { name: 'BACKUP_SCHEDULE_NAME', value: 'daily-user-data' }
            { name: 'BACKUP_MAX_ARCHIVE_BYTES', value: '52428800' }
            { name: 'BACKUP_MAX_UNCOMPRESSED_BYTES', value: '209715200' }
            { name: 'BACKUP_MAX_ENTRY_BYTES', value: '52428800' }
            { name: 'BACKUP_MAX_ZIP_FILES', value: '9' }
            { name: 'BACKUP_MAX_RECORDS', value: '200000' }
            { name: 'BACKUP_MAX_JSON_DEPTH', value: '30' }
            { name: 'BACKUP_MAX_EXPANSION_RATIO', value: '100' }
            { name: 'COSMOSDB_ENDPOINT', value: cosmosEndpoint }
            { name: 'COSMOSDB_KEY', secretRef: 'cosmosdb-key' }
          ]
        }
      ]
    }
  }
  dependsOn: [
    blobContributor
    tagWriter
    lifecycle
  ]
}

output storageAccountId string = storage.id
output containerId string = backupContainer.id
output jobId string = job.id
