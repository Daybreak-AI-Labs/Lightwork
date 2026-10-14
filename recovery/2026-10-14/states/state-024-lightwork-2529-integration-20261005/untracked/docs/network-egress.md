# Network destinations for public information lookups

Lightwork can let analyst specialists look up public information while they work. This is enabled by default in a standard installation. The specialists receive only the sources relevant to their business area; a task does not contact every source automatically.

When a lookup runs, Lightwork sends the search terms, filters or location needed for that request to the listed public service, then receives public information in response. A request may reveal a company, product, medicine, legal matter or other subject your team is researching. These tools do not change records at those sources. Some search APIs use HTTP POST even though the lookup is read-only, and some sources require a service key.

An installation administrator can switch off these built-in public lookups in **Settings → All settings → Use public sources to check agent answers**. Turning them off removes these tools from analyst specialists. It does not turn off your AI service, business-system connections, chat services, webhooks or optional tools.

This is the destination list for Lightwork's built-in public-reference connectors. It is not a complete list of every network service your installation can use: AI providers, connected business systems, chat services, webhooks and optional tools have their own destinations and data flows. Review those connections and their own privacy terms before using sensitive information.

| Public information source | Destination |
|---|---|
| FRED (St. Louis Fed) economic data | `api.stlouisfed.org` |
| SEC EDGAR company filings & XBRL facts | `data.sec.gov` |
| U.S. Treasury Fiscal Data | `api.fiscaldata.treasury.gov` |
| World Bank Open Data | `api.worldbank.org` |
| IMF DataMapper | `www.imf.org` |
| FDIC BankFind (institutions & financials) | `banks.data.fdic.gov` |
| Bureau of Economic Analysis | `apps.bea.gov` |
| U.S. Census Bureau data | `api.census.gov` |
| Bureau of Labor Statistics v1 | `api.bls.gov` |
| EIA energy data | `api.eia.gov` |
| Alpha Vantage market data | `www.alphavantage.co` |
| Finnhub market data | `finnhub.io` |
| Polygon.io market data | `api.polygon.io` |
| OpenFIGI security-identifier mapping | `api.openfigi.com` |
| U.S. Federal Register | `www.federalregister.gov` |
| Electronic Code of Federal Regulations | `www.ecfr.gov` |
| Regulations.gov dockets & comments | `api.regulations.gov` |
| CourtListener case law & dockets | `www.courtlistener.com` |
| GovInfo (bills, CFR, public laws) | `api.govinfo.gov` |
| USAspending federal awards/spending | `api.usaspending.gov` |
| SAM.gov entity registration & exclusions | `api.sam.gov` |
| Open States (state legislatures) | `v3.openstates.org` |
| PatentsView (USPTO patent data) | `search.patentsview.org` |
| GLEIF Legal Entity Identifier (LEI) registry | `api.gleif.org` |
| OpenCorporates company registry | `api.opencorporates.com` |
| UK Companies House | `api.company-information.service.gov.uk` |
| openFDA (drug/device/food adverse events, recalls, labels) | `api.fda.gov` |
| NPPES NPI Registry (US healthcare providers) | `npiregistry.cms.hhs.gov` |
| ClinicalTrials.gov v2 | `clinicaltrials.gov` |
| RxNorm/RxNav drug normalization | `rxnav.nlm.nih.gov` |
| PubMed/NCBI E-utilities | `eutils.ncbi.nlm.nih.gov` |
| US National Weather Service | `api.weather.gov` |
| NOAA Climate Data Online | `www.ncdc.noaa.gov` |
| OpenWeather | `api.openweathermap.org` |
| EPA Envirofacts environmental data | `data.epa.gov` |
| Climatiq carbon-emission factors/estimates | `api.climatiq.io` |
| Carbon Interface emission estimates | `www.carboninterface.com` |

The in-product Help page shows whether these lookups are currently enabled and renders the destination list directly from the installed connector catalog.

## Optional Skill Store catalog

When Lightwork fetches public catalog entries—for example, while someone browses the Skill Store or another catalog-backed list—it requests the public catalog index. The default index is hosted at `raw.githubusercontent.com` (the `cdayAI/awesome-maverick` catalog path). This request retrieves public catalog metadata and does not include task text. An administrator can configure a different catalog index, which changes the destination for that installation. Installing a catalog item is a separate action; Lightwork fetches the reviewed source, which may use a different website.
