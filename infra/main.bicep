targetScope = 'resourceGroup'

@description('Base name for all resources')
param appName string = 'alkass-translation'

@description('Azure region')
param location string = resourceGroup().location

@description('Azure Speech custom endpoint URL')
param speechEndpoint string

@description('Azure Translator custom endpoint URL')
param translatorEndpoint string

@description('Azure Speech region')
param speechRegion string = 'qatarcentral'

@description('Azure Translator region')
param translatorRegion string = 'qatarcentral'

@description('Virtual network CIDR for the Container Apps environment')
param virtualNetworkAddressPrefix string = '10.42.0.0/16'

@description('Dedicated infrastructure subnet CIDR for Azure Container Apps workload profiles environment. Minimum supported size is /27.')
param infrastructureSubnetAddressPrefix string = '10.42.0.0/27'

// ─── Log Analytics ───
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${appName}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ─── Networking for fixed outbound IP ───
resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${appName}-vnet'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        virtualNetworkAddressPrefix
      ]
    }
  }
}

resource natPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${appName}-nat-pip'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAddressVersion: 'IPv4'
    publicIPAllocationMethod: 'Static'
    idleTimeoutInMinutes: 4
  }
}

resource natGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: '${appName}-nat'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 10
    publicIpAddresses: [
      {
        id: natPublicIp.id
      }
    ]
  }
}

resource infrastructureSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: virtualNetwork
  name: 'aca-infra'
  properties: {
    addressPrefix: infrastructureSubnetAddressPrefix
    natGateway: {
      id: natGateway.id
    }
    delegations: [
      {
        name: 'acaDelegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

// ─── Container Apps Environment ───
resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${appName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnet.id
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// ─── Container Registry ───
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: '${replace(appName, '-', '')}acr${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

// ─── User-Assigned Managed Identity ───
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: location
}

// ─── Role: Cognitive Services User (for Speech + Translator) ───
@description('Cognitive Services User role ID')
var cognitiveServicesUserRole = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource speechRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, identity.id, cognitiveServicesUserRole, 'speech')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRole)
    principalType: 'ServicePrincipal'
  }
}

// ─── Container App ───
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: {
    'azd-service-name': 'web'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: '${acr.properties.loginServer}/${appName}:latest'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'AZURE_SPEECH_ENDPOINT', value: speechEndpoint }
            { name: 'AZURE_SPEECH_REGION', value: speechRegion }
            { name: 'AZURE_TRANSLATOR_ENDPOINT', value: translatorEndpoint }
            { name: 'AZURE_TRANSLATOR_REGION', value: translatorRegion }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'PORT', value: '8000' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output identityClientId string = identity.properties.clientId
output outboundWhitelistIp string = natPublicIp.properties.ipAddress
