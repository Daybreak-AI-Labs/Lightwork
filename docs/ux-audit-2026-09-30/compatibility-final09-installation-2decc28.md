# Python 3.10/3.11 final09 installation evidence

The private Python **3.10.19** and **3.11.14** environments contain the eight wheels built from **`2decc28d5a2709ec1d362bdfb8499aaa13aacfce`**. Both native Windows attempts, each labeled `final09-install-01`, passed. This installation checkpoint adds **no collection or application-test pass claim**; subsequent compatibility runs need separate receipts.

| Check | Python 3.10 | Python 3.11 |
| --- | ---: | ---: |
| Installed distributions | 100 | 96 |
| Unchanged third-party pins | 92 | 88 |
| First-party wheels force-reinstalled | 8 | 8 |
| Installed entries compared to exact wheel bytes | 4,856 | 4,856 |
| Installed RECORD entries hash/size verified | 4,887 | 4,887 |
| Rewritten RECORD entries without self-hashes | 8 | 8 |
| Installer child commands returning zero | 15 | 15 |
| Independently reconciled retained output hashes | 67 | 67 |
| Pip check, extras, constraints, private origins | Passed | Passed |

Installation used exact local wheel paths with `--force-reinstall --no-index --no-deps --no-compile`. Third-party names and versions remained unchanged. Both installs checked archive hashes, installed payload bytes, RECORD hashes/sizes, local-wheel provenance and generated entry points. Non-importing metadata and `find_spec` checks resolved all eight packages inside each private environment. These origin checks were part of the native installs; there was **no separate cross-account readiness probe**. The source checks recorded the frozen SHA and a clean checkout. Both final09 attempts recorded no failed commands.

New wheel metadata matched the immediately prior final08 evidence at `57a027adfdd8df955d33aaea88ec7089aafc6c60` and the original final06 dependency baseline at `3640fcf7906d3f7129df1b34bc1718492a164bcf`. Original06 plans, provenance and constraints retain their original labels and 92/88 external pins. Historical final06/final08 archives were not reopened; their hash-bound receipts supply prior identity and metadata evidence. New final09 wheels and installed payload were freshly checked. Earlier tests and final08 failed attempts remain historical evidence, without relabeling.

The live final08 source/test bindings and `final08-binding.json` were preserved byte-for-byte in `audit/compat-interpreters/retained-before-final09-01`. Each binding was replaced individually with `os.replace`; this was **not a two-file atomic transaction**. The final receipt followed both replacements. Its SHA-256 is `531a8c077ba9afa79977d30fa573171388b52c41177626b6428ee26a8a0dc7f9`. It binds both installations to final09 test/product source; the existing compatibility runner requires matching successful installation proof before a full phase.

The Node receipt has SHA-256 `2db56164ddbd5e05c0315d518a7de13cbff28bd3878c97e491ad6ac05f7fa17a`. It verifies copied dependencies and private resolution for dashboard DOM tests, browser tests and representative Python-launched Node calls inheriting the checkout directory. Its status remains `passed_checkout_only_temp_shadowing`: an external temporary-directory probe resolves the three Playwright packages from the nearer `C:/Users/Cstep/node_modules` ancestor despite `NODE_PATH`. The checkout supplies jsdom 29.1.1, parse5 8.0.1 and Playwright 1.58.2. Arbitrary temporary scripts and JavaScript/browser execution are not qualified by this receipt.

Generated Windows launchers were checked against installed RECORD and declared entry points, not nonexistent wheel launcher entries. Eight rewritten RECORD files lack a self-hash. This evidence does not establish that no unlisted stale file exists.

The [JSON evidence](compatibility-final09-installation-2decc28.json), SHA-256 `9e5d75ea04d1311ba7ea5fc398a3d1a5109dac7681de806b632aa317300bc986`, records all eight wheel hashes and their 06/08 lineage, commands/budgets, receipt/helper/provenance hashes, origins, preservation and limits. Raw receipts remain task-local under `audit/compat-interpreters`; the [final08 evidence](compatibility-final08-installation-57a027a.md) is unchanged.
