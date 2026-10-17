variable "tenancy_ocid" { type = string }
variable "user_ocid" { type = string }
variable "fingerprint" { type = string }
variable "private_key_path" { type = string }
variable "compartment_ocid" { type = string }

variable "region" {
  type    = string
  default = "us-ashburn-1"
}

variable "shape" {
  description = "Always-free ARM shape"
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "ocpus" {
  type    = number
  default = 4
}

variable "memory_gb" {
  type    = number
  default = 24
}

variable "ssh_public_key" {
  description = "Contents of the public key that may SSH to the VM"
  type        = string
}

variable "admin_cidr" {
  description = "CIDR allowed to SSH (your address /32)"
  type        = string
}

variable "ghcr_owner" {
  description = "GitHub owner of the ghcr.io images (lowercase)"
  type        = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "admin_key" {
  description = "HSS_ADMIN_KEY for the interleaving readout"
  type        = string
  sensitive   = true
}
