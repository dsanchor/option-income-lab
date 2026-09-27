param location string
param environmentId string
param apiName string
param frontendName string
param apiImage string
param frontendImage string
param apiConfig object
param frontendConfig object
param cosmosEndpoint string

@secure()
param cosmosKey string

param foundryEndpoint string

@secure()
param foundryKey string

param defaultModelDeployment string

@secure()
param telegramBotToken string = ''

@secure()
param telegramChatId string = ''

@secure()
param entraClientSecret string = ''

param frontendExternal bool = false

param tags object

var telegramSecrets = empty(telegramBotToken) ? [] : [
  {
    name: 'telegram-bot-token'
    value: telegramBotToken
  }
  {
    name: 'telegram-chat-id'
    value: telegramChatId
  }
]

var telegramEnv = empty(telegramBotToken) ? [] : [
  {
    name: 'TELEGRAM_BOT_TOKEN'
    secretRef: 'telegram-bot-token'
  }
  {
    name: 'TELEGRAM_CHAT_ID'
    secretRef: 'telegram-chat-id'
  }
]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiName
  location: location
  tags: tags
  identity: {
    type: 'None'
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: apiConfig.ingress.targetPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: concat([
        {
          name: 'cosmosdb-key'
          value: cosmosKey
        }
        {
          name: 'foundry-api-key'
          value: foundryKey
        }
      ], telegramSecrets)
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: apiConfig.cpu
            memory: apiConfig.memory
          }
          env: concat([
            {
              name: 'AI_PROVIDER'
              value: 'azure'
            }
            {
              name: 'MODEL_DEPLOYMENT'
              value: defaultModelDeployment
            }
            {
              name: 'AZURE_AI_PROJECT_ENDPOINT'
              value: foundryEndpoint
            }
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'foundry-api-key'
            }
            {
              name: 'COSMOSDB_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'COSMOSDB_KEY'
              secretRef: 'cosmosdb-key'
            }
          ], telegramEnv)
          probes: [
            {
              type: 'Liveness'
              tcpSocket: {
                port: apiConfig.ingress.targetPort
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
            {
              type: 'Readiness'
              tcpSocket: {
                port: apiConfig.ingress.targetPort
              }
              initialDelaySeconds: 5
              periodSeconds: 5
            }
            {
              type: 'Startup'
              tcpSocket: {
                port: apiConfig.ingress.targetPort
              }
              initialDelaySeconds: 1
              periodSeconds: 3
              failureThreshold: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiConfig.minReplicas
        maxReplicas: apiConfig.maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

resource frontend 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendName
  location: location
  tags: tags
  identity: {
    type: 'None'
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: empty(entraClientSecret) ? [] : [
        {
          name: 'entra-client-secret'
          value: entraClientSecret
        }
      ]
      ingress: {
        external: frontendExternal
        targetPort: frontendConfig.targetPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: frontendImage
          resources: {
            cpu: frontendConfig.cpu
            memory: frontendConfig.memory
          }
          env: [
            {
              name: 'API_BASE_URL'
              value: 'https://${api.properties.configuration.ingress.fqdn}'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              tcpSocket: {
                port: frontendConfig.targetPort
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
            {
              type: 'Readiness'
              tcpSocket: {
                port: frontendConfig.targetPort
              }
              initialDelaySeconds: 5
              periodSeconds: 5
            }
            {
              type: 'Startup'
              tcpSocket: {
                port: frontendConfig.targetPort
              }
              initialDelaySeconds: 1
              periodSeconds: 3
              failureThreshold: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: frontendConfig.minReplicas
        maxReplicas: frontendConfig.maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

output apiId string = api.id
output frontendId string = frontend.id
output apiFqdn string = api.properties.configuration.ingress.fqdn
output frontendFqdn string = frontend.properties.configuration.ingress.fqdn
