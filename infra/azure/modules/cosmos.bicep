param location string
param accountName string
param databaseName string
param containers array
param tags object

var symbolsIncludedPaths = [
  { path: '/symbol/?' }
  { path: '/doc_type/?' }
  { path: '/timestamp/?' }
  { path: '/watchlist/covered_call/?' }
  { path: '/watchlist/cash_secured_put/?' }
  { path: '/agent_type/?' }
  { path: '/activity/?' }
]

var symbolsExcludedPaths = [
  { path: '/reason/*' }
  { path: '/raw_response/*' }
  { path: '/analysis_context/*' }
  { path: '/*' }
]

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      { name: 'EnableServerless' }
    ]
    publicNetworkAccess: 'Enabled'
    minimalTlsVersion: 'Tls12'
    disableLocalAuth: false
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

resource containerResources 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [
  for container in containers: {
    parent: database
    name: container.name
    properties: {
      resource: {
        id: container.name
        partitionKey: {
          paths: [container.partitionKey]
          kind: 'Hash'
          version: 2
        }
        defaultTtl: container.ttl
        indexingPolicy: container.indexing == 'symbols'
          ? {
              indexingMode: 'consistent'
              automatic: true
              includedPaths: symbolsIncludedPaths
              excludedPaths: symbolsExcludedPaths
            }
          : {
              indexingMode: 'consistent'
              automatic: true
              includedPaths: [
                { path: '/*' }
              ]
              excludedPaths: [
                { path: '/"_etag"/?' }
              ]
            }
      }
    }
  }
]

output accountId string = account.id
output endpoint string = account.properties.documentEndpoint
