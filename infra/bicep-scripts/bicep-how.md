dim02accwse12345    Container registry
assets-app            Container App
assets-env          Container Apps Environment
assets-app-pull         Managed Identity
log-assets       Log Analytics workspace


clean up


# Set this to the resource group containing the five resources.
$ResourceGroup = "rg_gtgh_data_15"

# Check the resources before deleting them.
az resource list `
  --resource-group $ResourceGroup `
  --query "[?contains(['dim02accwse12345','assets-app','assets-env','assets-app-pull','log-assets'], name)].{Name:name, Type:type}" `
  --output table


  # 1. Delete the app before its environment and pull identity.
az containerapp delete `
  --name assets-app `
  --resource-group $ResourceGroup `
  --yes

# 2. Delete the Container Apps environment.
az containerapp env delete `
  --name assets-env `
  --resource-group $ResourceGroup `
  --yes

# 3. Delete the managed identity.
az identity delete `
  --name assets-app-pull `
  --resource-group $ResourceGroup

# 4. Delete the registry.
az acr delete `
  --name dim02accwse12345 `
  --resource-group $ResourceGroup `
  --yes

# 5. Delete the Log Analytics workspace.
az monitor log-analytics workspace delete `
  --workspace-name log-assets `
  --resource-group $ResourceGroup `
  --yes


  