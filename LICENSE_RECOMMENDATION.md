# License recommendation · MIT proposed

MIT is the recommended license for the project's original host tooling and documentation once the rights holder approves the exact source and identifies the proper copyright holder. It is short, standard, permits modification and redistribution including commercial use, and retains the copyright and permission notices. Noncommercial project intent belongs in project priorities. It need not restrict commercial use. [OSI MIT license](https://opensource.org/license/mit)

| Decision factor | MIT | Apache-2.0 |
| --- | --- | --- |
| Community and commercial use | Both allowed, with notice retention | Both allowed, with more detailed redistribution instructions |
| Patents | No express patent grant or patent-litigation termination clause in the text | Express grant for covered contributor patent claims and specified patent-litigation termination |
| Notices and changes | Retain copyright and permission notices in copies or substantial portions | Provide license, mark changes, retain relevant notices and carry applicable upstream NOTICE attribution |
| Inbound contributions | Document rights verification and inbound licensing for incorporated patches | Includes an express default contribution-license provision, subject to its terms and separate agreements |
| Fit for Hantek CLI | Preferred for a small, independently written compatibility tool and MIT-reference lineage | Viable if the owner values an explicit patent grant and accepts the added notice process |

Neither choice clears the vendor-derived table or re-licenses separately installed dependencies. MIT fits the current goal unless the owner identifies a specific need for Apache's patent terms. If Apache is selected later, preserve the original MIT notice for any covered community material. [Apache License 2.0, §§2–6](https://www.apache.org/licenses/LICENSE-2.0) · [Provenance resolution](SOURCE_PROVENANCE_RESOLUTION.md)

The root [proposed LICENSE](LICENSE) contains canonical MIT terms with a copyright placeholder. Package metadata does not declare an active project license. After rights-holder approval, replace the placeholder, set approved package license metadata, then build and audit a fresh immutable candidate. This proposal applies no outbound grant on behalf of the operator.
