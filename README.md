# Uproot Labs

**Uproot Labs** is a hands-on learning and automation toolkit designed to help **new and upcoming network engineers** build real skills through **practical lab environments**.

This project is intended to be used **alongside predefined EVE-NG lab topologies**, allowing you to move beyond theory and work with realistic network scenarios.

The focus is on:
- learning how packets actually move through a network
- practicing real troubleshooting workflows
- understanding how automation can be applied to networking tasks

---

## Uses

This tool can be beneficial for learning things like:

- **Basics of packet forwarding**
  - Routing, interfaces, gateways, and reachability
- **Hands-on troubleshooting practice**
  - Diagnosing broken paths, misconfigurations, and failures
- **Understanding network automation**
  - Using Python and APIs to inspect and validate network behavior

This is not a simulator or a GUI-heavy teaching tool, it’s meant to feel like working on an actual network.

---

## How it works

Uproot Labs is designed to be used with **specific EVE-NG lab topologies**.

Options for configuring EVE-NG:
- Stand up the labs **manually**  
  → *YouTube walkthrough:* https://youtu.be/lfiP-Zeb96o?si=OlBHXkaZJNyDcHVx 

- Or install them via **automation**  
  → *Automated install video:* Video to be released shortly


---

## YouTube Channel

This project is developed alongside my **YouTube channel** https://www.youtube.com/@ryan-ashcraft.

The channel is actively updated with:
- Lab walkthroughs
- Network fundamentals explained with real examples
- Automation demonstrations that directly use this tool

Each lab in this repository maps back to one or more videos on the channel.  Additional labs will be added as new videos are produced.

---

## Lab Files & Downloads

Prebuilt EVE-NG lab files, images, and setup scripts are hosted separately.

📦 **Downloads (EVE-NG topologies, scripts, images):**  
https://mega.nz/folder/jpQiGDjZ#TI_WuQN5bQ_dFUUJF2bp3w

These files are required to run the labs as demonstrated in the videos.

---

## Usage

### Installation

Manual installation (For use when manually installing EVE-NG lab from scratch): 

```bash
##from Ubuntu host in EVE-NG Lab, as seen in videos referenced above:
wget https://github.com/uprootnetworks/uproot-labs/archive/refs/tags/v.0.2beta.zip

unzip v.0.2beta.zip
./uproot-labs/lab/lab1/setup.sh

```
If you have installed your EVE-NG lab automatically using the templates and zip file above, you do not need to manually install the Uproot Labs package as it will already be present on your Ubuntu host.

### Installing Eve-NG Lab from Files Above
- Download files from https://mega.nz/folder/jpQiGDjZ#TI_WuQN5bQ_dFUUJF2bp3w
- Step 1: scp LabSetup.zip to eve-ng host
- Step 2: ssh to eve-ng host and run:
  ```
  unzip LabSetup.zip
  ./LabSetup/setup.sh
  ```
- Step 3: Upload Lab[x].unl.zip to eve-ng UI as shown in video - [link provided soon]
- Step 4: Power on Lab


### Updating

Additional labs and tools will be released as new videos are produced.  To update your existing lab host for bug fixes, new test cases, etc. simply run this command from your Ubuntu host:
```
uproot update
```

### Tool Usage
Run command 'uproot {labId} --{arg}|-{arg}' from command line of the Ubuntu host in your lab.  Available arguments are listed below:

Example: 'uproot lab1 -a' will introduce faults to every node in the lab environment, which you then need to troubleshoot to dig yourself out.  If you become stumped, the command 'uproot lab1 -d' will reset your lab environment back to default settings.
```
root@ubuntu:~# uproot lab1 --help
usage: break_things.py [-h] [-a] [-s] [-r] [-f] [-d]

options:
  -h, --help      show this help message and exit

NETWORK:
  -a, --all       Reconfigures all available options - a.k.a breaks everything :)
  -s, --switch    Adds incorrect configuration to Branch Switch1
  -r, --router    Reconfigures SP-Router1 and SP-Router2 with random faults
  -f, --firewall  Reconfigures Branch-FW and App-FW with random faults
  -d, --default   Restores all nodes to default settings
root@ubuntu:~# 
```
---
## Requirements
	•	EVE-NG installed either as baremetal or nested within a hypervisor (Tested with Proxmox and VMware ESXI)
	•	Internet access (for updates)

Additional requirements may apply per lab.
---
## Disclaimer

This tool is intended for lab and learning environments only.

It is provided as-is, with no guarantees.
Do not run against production networks.
