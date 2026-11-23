# Live research and version selection

Load this reference whenever choosing a new runtime, framework or library, proposing an upgrade, using recently introduced APIs, or making claims about current support or security.

This file names no version on purpose. Any number written here would be stale by the time it is read, so the method below is what carries over.

## Verification checklist

1. Inspect the repository: runtime pins, `package.json` engines, manifest, lockfile, CI matrix, container base images, hosting constraints, and supported operating systems and architectures.
2. Query primary sources: the official release and support page, versioned official docs, signed releases and changelog, official registry metadata, and security advisories.
3. Distinguish stable, Active LTS, Maintenance LTS, Current, alpha, beta, release candidate, and end-of-life. Prefer a supported LTS line for production runtimes unless project requirements dictate otherwise.
4. Confirm the exact version and patch, release date, compatibility matrix, Node.js and TypeScript peer requirements, licensing, and migration steps. Recheck right before an actual upgrade.
5. Decide whether the new benefits justify the upgrade risk. Prefer a compatible supported version over an untested major jump.
6. Write down the checked date (YYYY-MM-DD), selected version, source URL, and rationale in the final message. If research tools are unavailable, report that verification could not be performed.

## Example commands for an npm project

```bash
node --version
npm --version
npm view <package-name> version
npm view <package-name> dist-tags --json
npm view <package-name> engines --json
npm ls <package-name>
```

Choose commands appropriate to the actual project. Registry output is evidence for published versions. It is not by itself evidence of framework compatibility or of production support for a Node.js line. Use official release notes and support policies for that.

## Where to check Node.js

- Release lines and their support status: https://nodejs.org/en/about/previous-releases
- Current downloads and patch versions: https://nodejs.org/en/download/
- Release policy and schedule: https://github.com/nodejs/Release

Read the status of each release line from these pages on the day of the decision. For a new production service, prefer the Active LTS line that the dependencies support over a Current or prerelease line. Check again before every new project or upgrade.

## Reporting format

The block below is an illustration of the format only. Every value in angle brackets is a placeholder, not a real version or a recommendation.

```text
Versions researched (<YYYY-MM-DD>)
- Runtime: <name> <version> (<support status>); source: <official URL>
- Package: <name> <version>; compatibility checked against <runtime version>; source: <official URL>
- Why: <short compatibility, security or maintenance reason>
```

If live verification was not possible, say `Not verified live; used existing repository versions` instead of guessing.
