// Azure foundation (spec 6.6). Resources go here; see docs/azure_design.md.
targetScope = 'resourceGroup'

@allowed(['dev', 'prod'])
param environment string
param location string = resourceGroup().location
