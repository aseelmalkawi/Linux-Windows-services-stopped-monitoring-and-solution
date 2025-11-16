import os
import sys
import boto3
from botocore.exceptions import ClientError

def get_instance_ids_by_name(names, region):
    """Resolve instance IDs from EC2 using Hostname or Name tags."""
    client = boto3.client("ec2", region_name=region)

    reservations = []
    try:
        reservations += client.describe_instances(
            Filters=[{"Name": "tag:Name", "Values": names}]
        )["Reservations"]

        reservations += client.describe_instances(
            Filters=[{"Name": "tag:Hostname", "Values": names}]
        )["Reservations"]

    except ClientError as e:
        print(f"Error describing instances: {e}")
        sys.exit(1)

    instances = []
    for r in reservations:
        for inst in r["Instances"]:
            if inst["State"]["Name"] == "running":
                instances.append(inst["InstanceId"])

    return list(set(instances))  # deduplicate


def build_windows_script(services, action):
    return rf'''
$services="{services}" -split ","
foreach($svc in $services){{
    try {{
        $s = Get-Service -Name $svc -ErrorAction Stop
        if ("{action}" -eq "start") {{
            if ($s.Status -ne "Running") {{
                Start-Service -Name $svc -ErrorAction Stop
                Write-Host ("Started " + $s.DisplayName)
            }} else {{
                Write-Host ($s.DisplayName + " already running")
            }}
        }} elseif ("{action}" -eq "stop") {{
            if ($s.Status -ne "Stopped") {{
                Stop-Service -Name $svc -ErrorAction Stop
                Write-Host ("Stopped " + $s.DisplayName)
            }} else {{
                Write-Host ($s.DisplayName + " already stopped")
            }}
        }} elseif ("{action}" -eq "restart") {{
            Restart-Service -Name $svc -Force -ErrorAction Stop
            Write-Host ("Restarted " + $s.DisplayName)
        }}
    }} catch {{
        Write-Host ("Error with " + $svc + ": " + $_)
    }}
}}
'''


def build_linux_script(services, action):
    return f'''
services="{services}"
for svc in $services; do
    if systemctl list-unit-files | grep -q "$svc"; then
        if [ "{action}" = "start" ]; then
            sudo systemctl start "$svc" && echo "Started $svc" || echo "Failed to start $svc"
        elif [ "{action}" = "stop" ]; then
            sudo systemctl stop "$svc" && echo "Stopped $svc" || echo "Failed to stop $svc"
        elif [ "{action}" = "restart" ]; then
            sudo systemctl restart "$svc" && echo "Restarted $svc" || echo "Failed to restart $svc"
        fi
    else
        echo "Error: $svc not found on this system"
    fi
done
'''


def main():
    # Jenkins environment variables
    servers = os.getenv("server_names")        # comma-separated server names
    services = os.getenv("Service")       # comma-separated service names
    action = os.getenv("Action")          # start|stop|restart
    region = os.getenv("region", "us-east-1")  # fallback default region

    if not servers or not services or not action:
        raise ValueError("Missing required env vars: servers, Service, Action")

    server_list = [s.strip() for s in servers.split(",") if s.strip()]

    instance_ids = get_instance_ids_by_name(server_list, region)
    if not instance_ids:
        print(f"No running instances found for servers: {server_list}", flush=True)
        sys.exit(1)

    ec2 = boto3.client("ec2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)

    for instance_id in instance_ids:
        try:
            info = ec2.describe_instances(InstanceIds=[instance_id])
            platform = info["Reservations"][0]["Instances"][0].get("Platform", "Linux")
        except Exception as e:
            print(f"Failed to detect platform for {instance_id}: {e}", flush=True)
            continue

        if platform.lower() == "windows":
            script = build_windows_script(services, action)
            document = "AWS-RunPowerShellScript"
        else:
            script = build_linux_script(services, action)
            document = "AWS-RunShellScript"

        try:
            response = ssm.send_command(
                Targets=[{"Key": "instanceIds", "Values": [instance_id]}],
                DocumentName=document,
                Comment=f"{action.capitalize()} services: {services}",
                Parameters={"commands": [script]},
            )
            command_id = response["Command"]["CommandId"]
            print(f"{instance_id}: Sent {action} command (Command ID: {command_id})", flush=True)
        except ClientError as e:
            print(f"Error sending command to {instance_id}: {e}", flush=True)


if __name__ == "__main__":
    main()
