# Leads that need a contact address: 2026-10-28

The CRO leads in `needs_manual_contact`: Contact Lookup found no usable address for them in the ICH GCP profile or on their
own website, and Apollo is not callable on the free plan. **15** were waiting before today. **11** more were added today,
when the contact-lookup gate went to >= 40 for CROs.

## How to give a lead its address

Email the Nova Scout mailbox **from the operator address** (`settings.operator_email`, which must pass SPF and DKIM, as
for APPROVE / REJECT / EDIT). Write one line per lead, as many lines as you like, and no code is needed:

```text
CONTACT <lead domain> <email address> [name]
```

- `<lead domain>` is the **Domain** column below, exactly as written (a leading `www.` is ignored).
- `[name]` is optional. With a name, the first touch greets that person by first name, but only when the address
  carries their name (e.g. `jane.doe@…`). Without one, the greeting is "Hi there,".
- Each lead that is set gets the address as `verified`, moves to `contact_found`, and is drafted on the next Drafting tick
  (every 30 min). Its first touch approves itself under the normal rules and goes out in the recipient's business hours.
  The command itself sends nothing.
- A line is refused (and the acknowledgement says why) if the lead was already emailed, is disqualified, either domain is
  blocklisted, or the domain matches no lead. Two lines for one lead: the later one wins.
- You get one acknowledgement email listing every line as set or not set.

**Suggested lines.** The site itself published these addresses (see the notes before using them):

```text
CONTACT cebisinternational.com office@cebis-int.com
CONTACT mtz-clinical.pl info@pratia.com
CONTACT cpcmed.org info@cpcmed.org
CONTACT bopal.cl contact@bopalcro.com
CONTACT medsource.com newbusiness@ergomedgroup.com
```

## The 15 waiting before today

| Lead | Company | Domain (use in CONTACT) | Country | Score | Website (after redirects) | Contact page | Emails found on the site | Notes |
|---:|---|---|---|---:|---|---|---|---|
| 92 | CEBIS International | `cebisinternational.com` | Romania | 63 | https://cebisinternational.com/ | https://cebisinternational.com/contact/ (has a form) | `office@cebis-int.com` | Address is on cebis-int.com, not the lead's domain [V]. |
| 35 | Clinexa | `clinexa.com` | India | 58 | https://clinexa.com/ | https://clinexa.com/contact-us/ (has a form) | none |  |
| 212 | MTZ Clinical Research | `mtz-clinical.pl` | Poland | 54 | https://mtz-clinical.pl/ | https://mtz-clinical.pl/contact/ (has a form) | `agnieszka.dobaczewska@pratia.com`, `info@pratia.com`, `laboratorium@pratia.com` | The site's addresses are all @pratia.com: MTZ appears to be part of Pratia now [A]. |
| 500 | Archer Research | `archerresearch.eu` | Belgium | 51 | https://www.archerresearch.eu/ | https://www.archerresearch.eu (has a form) | none |  |
| 1527 | Nucleus Network | `nucleusnetwork.com` | Australia | 58 | https://www.nucleusnetwork.com/ | https://www.nucleusnetwork.com/contact/ (has a form) | none |  |
| 2107 | Westat | `westat.com` | USA | 73 | https://www.westat.com/ | https://www.westat.com/contact-us/ (has a form) | none |  |
| 2064 | Symbio | `symbioresearch.com` | USA | 58 | https://symbioresearch.com/ | https://symbioresearch.com/contact-us/ (has a form) | none |  |
| 1771 | CPC Clinical Research | `cpcmed.org` | USA | 56 | https://cpcclinicalresearch.org/ | https://cpcclinicalresearch.org/contact-us/ (has a form) | `info@cpcclinicalresearch.org`, `info@cpcmed.org` | cpcmed.org now redirects to cpcclinicalresearch.org [V]; Contact Lookup refuses a page on another host, which is why it missed info@cpcmed.org [A]. |
| 1765 | Comparative Biosciences, Inc | `compbio.com` | USA | 53 | https://compbio.com/ | https://compbio.com/contact-us (has a form) | none |  |
| 1997 | PROMETRIKA | `prometrika.com` | USA | 53 | https://www.prometrika.com/ | https://www.prometrika.com/contact/ (has a form) | none |  |
| 1928 | MedSource | `medsource.com` | USA | 51 | https://ergomedcro.com/ | https://ergomedcro.com/contact-us/ (has a form) | `info@ergomedgroup.com`, `marketing@ergomedgroup.com`, `ms-careers@ergomedgroup.com`, `newbusiness@ergomedgroup.com` | medsource.com redirects to ergomedcro.com: MedSource is part of Ergomed now [V redirect, A ownership]. Ergomed is a large group; check it is still a fit. |
| 3129 | CPQuali Pesquisa Clínica | `cpquali.com.br` | Brazil | 61 | https://cpquali.com.br/ | https://cpquali.com.br/contato/ (has a form) | none |  |
| 3124 | Stat Research S.A | `stat-research.com` | Argentina | 54 | https://stat-research.com/ | none found | none | Homepage fetched; no contact link or address in it [V]. |
| 707 | MD Biosciences | `mdbiosciences.com` | Switzerland | 67 | https://www.mdbneuro.com/ | none found | none | mdbiosciences.com redirects to mdbneuro.com [V]. No address or contact page found. |
| 1852 | Health Decisions | `healthdec.com` | USA | 53 | https://premier-research.com/ | https://premier-research.com/contact-us/ (has a form) | none | healthdec.com redirects to premier-research.com: part of Premier Research now [V redirect, A ownership]. Large CRO; check fit. |

## The 11 added today (score 40-49)

| Lead | Company | Domain (use in CONTACT) | Country | Score | Website (after redirects) | Contact page | Emails found on the site | Notes |
|---:|---|---|---|---:|---|---|---|---|
| 1719 | Bright Pharmaceutical Services | `brightps.com` | USA | 49 | https://brightps.com/ | none found | none | Homepage fetched; no contact link or address in it [V]. |
| 1446 | Frontage Laboratories | `frontagelab.com.cn` | China | 49 | https://frontagelab.com.cn/zh_cn/ | https://frontagelab.com.cn/zh_cn/%e6%8a%95%e8%b5%84%e8%80%85%e5%85%b3%e7%b3%bb/investor-inquiries/ (has a form) | `ir@frontagelab.com.cn` | Only address found is ir@ (investor relations), which is not a sales inbox [V]. |
| 3123 | Centrum Badan Klinicznych PI-House sp. z o.o | `pihouse.pl` | Poland | 49 | https://badaniakliniczne.pentahospitals.pl/osrodki/gdansk/ | https://badaniakliniczne.pentahospitals.pl/kontakt/ (has a form) | `badania.kliniczne@pentahospitals.pl`, `gdansk1@pentahospitals.pl`, `julia.falkowska@pentahospitals.pl`, `kinga.rzeszkowska@pentahospitals.pl`, `patrycja.markiewicz@pentahospitals.pl`, `stella.grodzicka@pentahospitals.pl` | pihouse.pl redirects to a Penta Hospitals clinical-research site page [V]: now a hospital research site, which the CRO template never drafts [A]. |
| 566 | PharmaFacts | `pharmafacts.com` | Germany | 48 | https://pharmafacts.com/ | none found | none | Homepage fetched; no contact link or address in it [V]. |
| 704 | Appletree | `appletree-cig.com` | Switzerland | 48 | https://tfscro.com/ | https://tfscro.com/contact/ | none | appletree-cig.com redirects to tfscro.com (TFS HealthScience) [V redirect]. Check fit. |
| 1807 | DSP Clinical Research | `dspclinical.com` | USA | 48 | https://dspclinical.com/ | https://dspclinical.com/contact-us/ | none | Contact page has no address and no form in its HTML [V] (it may load one by script). |
| 1806 | Labnetworx | `drugdevelopment.labcorp.com` | USA | 48 | https://www.labcorp.com/biopharma | https://www.labcorp.com/clinical-drug-testing/support/contact-labcorp-account-representative (has a form) | none | Lead is a Labcorp drug-development page [V]; Labcorp is one of the largest CROs. Probably not a fit; consider leaving it. |
| 2014 | Research Dynamics | `resdyncg.com` | USA | 48 | not reachable: HTTPError: HTTP Error 404: Not Found | - | none | resdyncg.com returned HTTP 404 on every URL tried [V]; the site may be gone. |
| 1715 | BOPAL | `bopal.cl` | Chile | 45 | https://www.bopalcro.com/ | none found | `contact@bopalcro.com` | bopal.cl redirects to bopalcro.com [V]. |
| 1832 | Fast-Track Drugs and Biologics | `fasttrackresearch.com` | USA | 44 | https://fasttrackresearch.com/ | none found | none | Homepage fetched; no contact link or address in it [V]. |
| 103 | The Micron Group | `micron-group.com` | South Africa | 43 | https://micron-group.com/ | https://micron-group.com/contact-us (has a form) | none |  |

## How this list was made

Read-only. The leads are `needs_manual_contact` rows whose `lead_company_type()` is CRO. Each website was fetched on
2026-10-28: the homepage, then up to two links whose URL or text says contact / kontakt / contacto / contato / impressum /
imprint (at most 3 requests per site, one site at a time). Every `mailto:` and every address written in the page text is
listed. Contact Lookup only accepts an address on the lead's own domain and never a careers / ir / press inbox, which is
why some listed here were not picked up automatically. "Has a form" means the page HTML contains a `<form>`.
