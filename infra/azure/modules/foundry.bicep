param location string
param accountName string
param projectName string
param deployments array
param tags object

var mini = first(filter(deployments, deployment => deployment.deploymentName == 'gpt-5.4-mini'))
var luna = first(filter(deployments, deployment => deployment.deploymentName == 'gpt-5.6-luna'))
var sol = first(filter(deployments, deployment => deployment.deploymentName == 'gpt-5.6-sol'))

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
    disableLocalAuth: false
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource miniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: mini.deploymentName
  sku: {
    name: mini.sku
    capacity: mini.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: mini.model
      version: mini.version
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [
    project
  ]
}

resource lunaDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: luna.deploymentName
  sku: {
    name: luna.sku
    capacity: luna.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: luna.model
      version: luna.version
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [
    miniDeployment
  ]
}

resource solDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: sol.deploymentName
  sku: {
    name: sol.sku
    capacity: sol.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: sol.model
      version: sol.version
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [
    lunaDeployment
  ]
}

output accountId string = account.id
output projectId string = project.id
output endpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
