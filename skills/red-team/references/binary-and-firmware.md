# Binary and firmware

This guide covers work on binaries, firmware images and devices. Static analysis comes first. Running code and writing to a device come later, and only under the rules below.

## Contents

- Ground rules
- Identify the file
- Static analysis
- Dynamic analysis
- Hardware that the operator owns

## Ground rules

- The file must be in scope by its SHA-256. Compute the hash yourself and check it before you open the file.
- Work on a copy inside the engagement folder. Keep the original unchanged.
- Do not run an unknown binary on a machine that holds other work.
- Do not upload a sample to an online analysis service. A sample can hold secrets, and some services publish what they receive.
- Check that each tool exists before you rely on it, and read its help. Flags differ between versions.

```sh
for tool in shasum file strings xxd binwalk objdump nm cmp; do command -v "$tool" >/dev/null || echo "missing: $tool"; done
```

## Identify the file

Compute the hash. Use whichever of these two tools the machine has:

```sh
shasum -a 256 firmware.bin
sha256sum firmware.bin
```

Check the hash against the scope file:

```sh
python3 "$SKILL_DIR/scripts/scope_check.py" --scope scope.json \
  --target 0000000000000000000000000000000000000000000000000000000000000000 \
  --technique static-analysis
```

Replace the row of zeros with the real hash. Then look at what kind of file it is:

```sh
file firmware.bin
xxd -l 256 firmware.bin
```

`file` reports the format. The first bytes often show a header, a magic number or a vendor name.

## Static analysis

Technique class: `static-analysis`. Nothing is run.

Pull out readable text and save it:

```sh
strings -n 8 firmware.bin > evidence/firmware-strings.txt
```

Search the text for version numbers, product names, file paths, addresses and command names. Version strings feed `vulnerability-triage.md`. If you see a password or a key, record where it is and redact the value.

List what is packed inside an image, look at its entropy, and unpack it into the engagement folder:

```sh
binwalk firmware.bin
binwalk -E firmware.bin
binwalk -e -C evidence/extracted firmware.bin
```

Long stretches of high entropy mean compressed or encrypted data. If the image is encrypted or signed, write that down. See the last section before you go further.

For an executable, read its headers, the libraries it needs, and its code:

```sh
objdump -h program
nm -u program
objdump -d program > evidence/program-disassembly.txt
```

On macOS, `otool -L program` lists the libraries a program links to. On Linux, the ELF reader from the binutils package gives the same kind of header and library information. For deeper reading, use a disassembler or a decompiler. Name the tool and its version in your notes.

In an unpacked file system, look at:

- start-up scripts and service configuration, to see what runs and with which privileges
- account files and default credentials. Report where they are. Using them needs the operator's yes.
- keys and certificates that are built in
- the update mechanism: where updates come from and how an image is checked before it is accepted
- web interface and API code
- third-party components and their versions

To compare two versions of a file or an image, start simply. `cmp old.bin new.bin` says whether they differ. For executables, a binary comparison tool can match functions between versions. Use it to learn what a vendor fix changed. See `vulnerability-triage.md`.

## Dynamic analysis

Technique class: `dynamic-analysis`. Only after static analysis, and only when the scope file allows it for this file.

- Use an isolated lab: a virtual machine or an emulator with no route to production systems or to the internet.
- Take a snapshot first, so the lab can be put back.
- Watch what the program opens, listens on and tries to contact. Use a debugger or a tracing tool for this. Check that it exists and read its help.
- Keep the outputs in the engagement folder and log each run.
- If the program tries to reach a real host, stop. That host needs its own scope check.

## Hardware that the operator owns

Technique classes: `hardware-testing` for bench work, and `firmware-modification` for any write. The device must be listed under `hardware` in the scope file. Check it by its identifier:

```sh
python3 "$SKILL_DIR/scripts/scope_check.py" --scope scope.json \
  --target SN-0001 --type hardware --technique hardware-testing
```

Bench work, in order of risk:

1. Look. Photograph the board. Note chip markings, test points and connectors. Read the public data sheets.
2. Listen. Watch a serial console or a bus without sending anything.
3. Read. Read the storage through a debug or programming interface.
4. Write. Only under the rule below.

Confirm voltage levels before you connect anything. A wrong connection can destroy a board. If you are not sure, ask the operator.

### The restore image rule

Before any write to a device, there must be a verified restore image.

1. Read the full storage of the device twice, with a programmer that suits the chip and its interface.
2. Confirm that the two reads are the same, and record the hash:

```sh
cmp read-1.bin read-2.bin
shasum -a 256 read-1.bin
```

3. Keep the image in the engagement folder, and a second copy where the operator says.
4. The operator confirms a tested way to write the image back to this device.
5. The operator adds the hash to the scope file as `restore_image_sha256` for this device, and sets `operator_owned` to true. Validate the scope file again.
6. The check for `firmware-modification` now passes. Each write still needs the operator's yes for that specific action.

If the two reads differ, or nobody has tested the way back, do not write to the device.

### Signed and encrypted images

A device that checks a signature before it accepts firmware is working as designed. This skill does not defeat signature or integrity checks on a device the operator does not own. On a device the operator owns and the scope file lists, report how the check works and what it covers. Ask the operator before any step that would get around it, and apply the restore image rule first.
