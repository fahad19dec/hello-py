# Compliance notes (India)

**This is not legal advice and not a compliance review.** It records what was
found while designing the platform, how sure each point is, and the questions a
qualified adviser and the broker must answer before live execution is enabled
for Indian retail users. Until then `LIVE_TRADING_ENABLED` stays `false`; the
service will not start with it on unless a reference to the signed-off review is
also configured.

Checked on 12 and 13 November 2026.

## Read directly from Zerodha

These come from Zerodha's own support page, developer forum and API
documentation.

| Finding | Effect on the platform |
|---|---|
| From 1 April 2026 a static IP is required for API order placement. Orders from an unregistered IP are rejected. Only order endpoints are checked; market data, order book and positions work from any IP. | Order placement must leave from a registered address. |
| The IP is set on the developer profile. Up to two can be registered, and it can be changed once a calendar week. The declaration is that the IP is used only by the account holder and immediate family (spouse, dependent children, dependent parents). A user who tried to add one IP to a second account was told each IP can be linked to one account. Staff said sharing among unrelated people can lead to suspension of API access. | One server address cannot serve unrelated customers through their own Kite keys. This rules out the simple hosted model for Zerodha. |
| Market and SL-M orders need market protection; a value of 0 is rejected. `-1` asks Zerodha to apply its automatic limit. | The adapter must send `market_protection` on every market order. |
| A limit of 10 orders per second per client. Above that the strategy must be registered with the exchange. | A per-client rate limiter is part of the executor. |
| A successful order call means the order was accepted, not executed. | Fills must come from postbacks or the order book. |
| The access token expires at 6:00 the next morning, and a login is needed each day. | The platform cannot refresh a session silently and must not try to bypass login or two-factor. |
| Asked whether the static-IP rule applies to Kite Publisher, staff answered that it does not. | A semi-automatic mode in which the customer confirms each order in Zerodha's own screen is a candidate route. |

Sources:
[Zerodha support: static IP](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip),
[Kite forum: preparing for SEBI's retail algo rules](https://kite.trade/forum/discussion/comment/52171),
[Kite Connect orders](https://kite.trade/docs/connect/v3/orders/),
[Kite Connect login flow](https://kite.trade/docs/connect/v3/user/).

## From summaries, not yet read in the original

These come from search summaries of news coverage, broker posts and NSE
documents. They are consistent with each other but the circulars themselves
were not read. Treat them as leads for the review.

| Point | Source |
|---|---|
| SEBI issued "Safer participation of retail investors in algorithmic trading" on 4 February 2025. | [Business Standard](https://www.business-standard.com/markets/capital-market-news/nse-lays-down-new-ground-rules-for-retail-algo-trading-to-boost-safety-and-compliance-125050600690_1.html), [Zerodha Z-Connect](https://zerodha.com/z-connect/general/a-comprehensive-overview-of-nses-circular-on-the-new-retail-algo-trading-framework) |
| API access is provided only through brokers, with a client-specific key and a broker-whitelisted static IP. | Same |
| Algo orders are tagged with an exchange-issued algo identifier. | Same |
| Algo providers are empanelled with the exchange. NSE has published empanelment notices for named providers. | Search summaries of NSE notices |
| Algorithms are classed as white box or black box. Black-box providers need Research Analyst registration and a research report per algorithm. | Search summaries |
| An NSE FAQ dated 3 November 2025 is summarised as saying algorithms developed by algo providers must be hosted on the trading member's server. If accurate, this bears directly on where this platform's execution may run. | [NSE FAQ](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail_Algo_03112025_NSE.pdf) |
| The framework became mandatory for brokers from 1 April 2026. Earlier dates in 2025 also appear in coverage. | Mixed secondary sources |

## Not researched yet

- MetaTrader: whether and how forex and CFD trading through offshore brokers is
  open to Indian residents under RBI and FEMA rules. This decides who the MT4
  and MT5 features may be offered to.
- Data retention periods, hosting and audit obligations for vendors.
- BSE's implementation standards.
- GST, payment gateway and consumer-protection obligations for the subscription.

## Routes for Zerodha, to be chosen by the review

| Route | How it works | Open question |
|---|---|---|
| Customer-run agent | The customer runs a small agent on a machine with their own registered static IP. The platform sends it validated instructions; the agent holds the Kite secret and places the order. Same model as the MT4 and MT5 bridge. | Whether a platform supplying the instructions still counts as an algo provider that needs empanelment. |
| Semi-automatic through Kite Publisher | The platform prepares the order; the customer confirms it in Zerodha's own screen. | Whether this meets users' needs; it is not hands-free. |
| Empanelled algo provider | A partnership with a broker, exchange empanelment, registered strategies, hosting as the framework requires. | Commercial and regulatory effort; the hosting requirement above. |
| Personal and family use | The operator's own developer account and static IP, for their own and immediate family's accounts. | Not a commercial multi-user service. |

## Questions the compliance review must answer

1. Which SEBI circulars and NSE and BSE standards apply to a platform that turns
   a customer's own TradingView alerts into orders, and in which role?
2. Does the platform need empanelment as an algo provider? If so, through which
   broker, and are the customer's own strategies white box?
3. Where may execution be hosted, given the NSE FAQ on hosting?
4. What must be recorded and retained, for how long, and who may audit it?
5. Which Zerodha terms govern a commercial service built on customers' Kite
   Connect keys, and is written permission needed?
6. What must users be told and acknowledge before live trading?
7. Who may be offered MT4 and MT5 execution?

## What the platform does meanwhile

- Live execution is off by default and cannot be switched on by one setting.
- Paper trading is independent of the gate.
- The design reserves an algo identifier on every order, a per-client order-rate
  limit, per-account egress routing and per-market enablement, so the answers
  above can be applied without redesign.
- Nothing in the product or its documentation claims regulatory approval or
  broker authorisation, or promises returns.
