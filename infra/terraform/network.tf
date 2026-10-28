# One network, two subnets, no public IPs. People reach the VMs through Azure Bastion in the portal;
# the VMs reach the internet (Windows Update, Microsoft 365, ServiceNow, GitHub) through Azure's
# default outbound access, which costs nothing, unlike a NAT gateway.
resource "azurerm_virtual_network" "lab" {
  name                = "vnet-${local.base}"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = local.location
  address_space       = [var.address_space]
  # The domain controller answers for corp.kestrel.internal and forwards everything else to Azure
  # DNS. Azure DNS is second, so the VMs still resolve internet names while the DC is being built or
  # is switched off.
  dns_servers = [var.dc_private_ip, "168.63.129.16"]
  tags        = local.tags
}

resource "azurerm_subnet" "servers" {
  name                            = "snet-servers"
  resource_group_name             = data.azurerm_resource_group.lab.name
  virtual_network_name            = azurerm_virtual_network.lab.name
  address_prefixes                = [var.servers_subnet_prefix]
  default_outbound_access_enabled = true
}

resource "azurerm_subnet" "clients" {
  name                            = "snet-clients"
  resource_group_name             = data.azurerm_resource_group.lab.name
  virtual_network_name            = azurerm_virtual_network.lab.name
  address_prefixes                = [var.clients_subnet_prefix]
  default_outbound_access_enabled = true
}

# Azure's default rules already allow traffic inside the network and deny everything from the
# internet. These rules make the intent explicit: RDP only through Bastion Developer, which connects
# from Azure's platform address 168.63.129.16.
resource "azurerm_network_security_group" "servers" {
  name                = "nsg-${local.base}-servers"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = local.location
  tags                = local.tags

  security_rule {
    name                       = "allow-bastion-rdp"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_address_prefix      = "168.63.129.16"
    source_port_range          = "*"
    destination_address_prefix = "*"
    destination_port_range     = "3389"
  }

  # AD DS, DNS, Kerberos, SMB and the rest, from the PC subnet to the domain controller only.
  security_rule {
    name                       = "allow-clients-to-dc"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_address_prefix      = var.clients_subnet_prefix
    source_port_range          = "*"
    destination_address_prefix = var.dc_private_ip
    destination_port_range     = "*"
  }

  security_rule {
    name                       = "deny-internet-in"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_address_prefix      = "Internet"
    source_port_range          = "*"
    destination_address_prefix = "*"
    destination_port_range     = "*"
  }
}

resource "azurerm_network_security_group" "clients" {
  name                = "nsg-${local.base}-clients"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = local.location
  tags                = local.tags

  security_rule {
    name                       = "allow-bastion-rdp"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_address_prefix      = "168.63.129.16"
    source_port_range          = "*"
    destination_address_prefix = "*"
    destination_port_range     = "3389"
  }

  security_rule {
    name                       = "deny-internet-in"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_address_prefix      = "Internet"
    source_port_range          = "*"
    destination_address_prefix = "*"
    destination_port_range     = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "servers" {
  subnet_id                 = azurerm_subnet.servers.id
  network_security_group_id = azurerm_network_security_group.servers.id
}

resource "azurerm_subnet_network_security_group_association" "clients" {
  subnet_id                 = azurerm_subnet.clients.id
  network_security_group_id = azurerm_network_security_group.clients.id
}

# Bastion Developer is free and needs no subnet or public IP. One session at a time, from the
# Azure portal only, which is all a lab needs.
resource "azurerm_bastion_host" "lab" {
  count               = var.enable_bastion ? 1 : 0
  name                = "bas-${local.base}"
  resource_group_name = data.azurerm_resource_group.lab.name
  location            = local.location
  sku                 = "Developer"
  virtual_network_id  = azurerm_virtual_network.lab.id
  tags                = local.tags
}
