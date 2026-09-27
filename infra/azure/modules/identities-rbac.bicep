param location string
param identityName string
param tags object

var tagWriterRoleGuid = guid(subscription().id, resourceGroup().name, 'backup-blob-tag-writer-v1')

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource tagWriterRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: tagWriterRoleGuid
  properties: {
    roleName: 'Option Income Lab Backup Blob Tag Writer v1'
    description: 'Write Blob index tags for the backup container; no other action.'
    type: 'CustomRole'
    assignableScopes: [
      resourceGroup().id
    ]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write'
        ]
        notDataActions: []
      }
    ]
  }
}

output identityId string = identity.id
output identityPrincipalId string = identity.properties.principalId
output identityClientId string = identity.properties.clientId
output tagWriterRoleDefinitionId string = tagWriterRole.id
