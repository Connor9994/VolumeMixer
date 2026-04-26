import json

with open('g:\\Users\\Administrator\\Desktop\\VolumeMixer\\svv_output.json', encoding='utf-16') as f:
    data = json.load(f)

apps = [i for i in data if i.get('Type') == 'Application']
devices = [i for i in data if i.get('Type') == 'Device']

# Check if any Application entry has a non-empty 'Default' field
has_default = [(a['Name'], a['Default']) for a in apps if a.get('Default', '').strip()]
print('Apps with non-empty Default field:', has_default if has_default else 'NONE')
print()

# Check the unique values of 'Default' field across all apps
default_vals = set(a.get('Default', '') for a in apps)
print('Unique Default values in apps:', default_vals)
print()

# Find the default render device
default_device = None
for d in data:
    if d.get('Type') == 'Device' and d.get('Default') == 'Render':
        default_device = d
        break

if default_device:
    print('=== Default Render Device ===')
    print(f"  Name: {default_device.get('Name')}")
    print(f"  Device Name: {default_device.get('Device Name')}")
    print(f"  Command-Line Friendly ID: {default_device.get('Command-Line Friendly ID')}")
    print()

    # Find apps on the default device
    default_dev_name = default_device.get('Device Name', '')
    on_default = [a for a in apps if a.get('Device Name') == default_dev_name]
    print(f'Apps on default device ("{default_dev_name}"):')
    for a in on_default:
        print(f"  {a['Name']}: Default='{a.get('Default','')}'")
    print()

    # Find apps NOT on the default device
    not_on_default = [a for a in apps if a.get('Device Name') != default_dev_name]
    print(f'Apps NOT on default device (sample):')
    for a in not_on_default[:5]:
        print(f"  {a['Name']}: Device='{a.get('Device Name','')}', Default='{a.get('Default','')}'")
    print()

# Check ALL fields for an app on default vs not on default
if default_device:
    default_dev_name = default_device.get('Device Name', '')
    on = [a for a in apps if a.get('Device Name') == default_dev_name]
    off = [a for a in apps if a.get('Device Name') != default_dev_name]
    
    if on and off:
        print('=== Comparing app ON default vs NOT on default ===')
        print('Keys where values DIFFER:')
        for key in on[0].keys():
            if on[0].get(key) != off[0].get(key):
                print(f"  {key}: ON='{on[0].get(key)}' vs OFF='{off[0].get(key)}'")
