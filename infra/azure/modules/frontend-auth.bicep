targetScope = 'resourceGroup'

param frontendName string
param tenantId string
param clientId string
param unauthenticatedClientAction string = 'RedirectToLoginPage'

resource frontend 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: frontendName
}

resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: frontend
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: unauthenticatedClientAction
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: clientId
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer: 'https://sts.windows.net/${tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${clientId}'
            clientId
          ]
          defaultAuthorizationPolicy: {
            allowedApplications: [
              clientId
            ]
          }
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: true
      cookieExpiration: {
        convention: 'FixedTime'
        timeToExpiration: '08:00:00'
      }
      nonce: {
        validateNonce: true
        nonceExpirationInterval: '00:05:00'
      }
    }
    httpSettings: {
      requireHttps: true
      routes: {
        apiPrefix: '/.auth'
      }
      forwardProxy: {
        convention: 'NoProxy'
      }
    }
  }
}

output authConfigId string = auth.id
