output "public_ip" {
  value = oci_core_instance.vm.public_ip
}

output "ui_url" {
  value = "http://${oci_core_instance.vm.public_ip}"
}

output "api_health" {
  value = "http://${oci_core_instance.vm.public_ip}/api/health"
}

output "ssh" {
  value = "ssh ubuntu@${oci_core_instance.vm.public_ip}"
}
