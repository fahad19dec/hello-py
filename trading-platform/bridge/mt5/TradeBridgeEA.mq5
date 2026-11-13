//+------------------------------------------------------------------+
//| TradeBridgeEA.mq5                                                |
//| Executes instructions from the trading platform in this terminal |
//|                                                                  |
//| The platform never receives your broker password. This Expert    |
//| Advisor authenticates with a device token, asks the platform for |
//| work, checks it against this terminal's own symbol rules, sends  |
//| it, and reports exactly what the terminal answered.              |
//|                                                                  |
//| Setup: see docs/metatrader-bridge.md                             |
//| Tools > Options > Expert Advisors > "Allow WebRequest for listed |
//| URL" must include the platform address.                          |
//+------------------------------------------------------------------+
#property copyright "Trading platform"
#property version   "1.00"
#property strict

input string ApiUrl              = "https://your-domain.example/api/v1/bridge"; // Platform bridge address
input string DeviceToken         = "";    // Device token shown once when you paired this account
input int    PollMilliseconds    = 1000;  // How often to ask for work
input int    DefaultDeviationPts = 20;    // Allowed slippage (points) when the alert sets no limit
input double MaxVolumePerOrder   = 0.0;   // Local safety cap in lots (0 = no cap)

string g_magic_text = "";
long   g_magic      = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(StringLen(DeviceToken) < 10)
     {
      Print("TradeBridge: set the DeviceToken input (shown when you paired this account).");
      return(INIT_PARAMETERS_INCORRECT);
     }
   EventSetMillisecondTimer(MathMax(PollMilliseconds, 250));
   Print("TradeBridge: started. Algo trading must be enabled for orders to be sent.");
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

//+------------------------------------------------------------------+
//| HTTP helpers                                                      |
//+------------------------------------------------------------------+
string UrlEncode(string text)
  {
   string out = "";
   uchar bytes[];
   int n = StringToCharArray(text, bytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   for(int i = 0; i < n; i++)
     {
      uchar c = bytes[i];
      if((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~')
         out += CharToString(c);
      else
         out += StringFormat("%%%02X", c);
     }
   return(out);
  }

// POST a form; returns the HTTP status (or -1) and fills `reply`.
int Post(string path, string form, string &reply)
  {
   char body[], result[];
   string headers = "Content-Type: application/x-www-form-urlencoded\r\nX-Device-Token: " + DeviceToken + "\r\n";
   string result_headers;
   StringToCharArray(form, body, 0, StringLen(form), CP_UTF8);
   ResetLastError();
   int status = WebRequest("POST", ApiUrl + path, headers, 10000, body, result, result_headers);
   if(status == -1)
     {
      int err = GetLastError();
      if(err == 4014)
         Print("TradeBridge: add ", ApiUrl, " to Tools > Options > Expert Advisors > Allow WebRequest.");
      else
         Print("TradeBridge: cannot reach the platform (error ", err, "). Will retry.");
      return(-1);
     }
   reply = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return(status);
  }

// Read "key=value" from a reply of such lines.
string Field(string reply, string key)
  {
   string lines[];
   int n = StringSplit(reply, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      int eq = StringFind(lines[i], "=");
      if(eq > 0 && StringSubstr(lines[i], 0, eq) == key)
        {
         string value = StringSubstr(lines[i], eq + 1);
         StringTrimRight(value);
         StringTrimLeft(value);
         return(value);
        }
     }
   return("");
  }

void Report(string id, string status, string extra)
  {
   string reply;
   string form = "id=" + UrlEncode(id) + "&status=" + status + extra;
   for(int attempt = 0; attempt < 5; attempt++)
     {
      int code = Post("/result", form, reply);
      if(code >= 200 && code < 500)
         return;            // recorded (or refused for a reason a retry cannot fix)
      Sleep(1000 * (attempt + 1));
     }
   Print("TradeBridge: could not report ", id, "; the platform will ask this terminal about it.");
  }

string Num(double value, int digits) { return(DoubleToString(value, digits)); }

//+------------------------------------------------------------------+
//| Main loop                                                         |
//+------------------------------------------------------------------+
void OnTimer()
  {
   string form = "login=" + (string)AccountInfoInteger(ACCOUNT_LOGIN)
               + "&server=" + UrlEncode(AccountInfoString(ACCOUNT_SERVER))
               + "&currency=" + UrlEncode(AccountInfoString(ACCOUNT_CURRENCY))
               + "&balance=" + Num(AccountInfoDouble(ACCOUNT_BALANCE), 2)
               + "&equity=" + Num(AccountInfoDouble(ACCOUNT_EQUITY), 2)
               + "&margin_free=" + Num(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2)
               + "&terminal=" + (string)TerminalInfoInteger(TERMINAL_BUILD)
               + "&positions=" + UrlEncode(PositionsText());
   string reply;
   int code = Post("/poll", form, reply);
   if(code != 200)
     {
      if(code == 401) Print("TradeBridge: the platform does not recognise this device token. Pair the account again.");
      return;
     }
   string type = Field(reply, "type");
   g_magic_text = Field(reply, "magic");
   if(g_magic_text != "") g_magic = StringToInteger(g_magic_text);
   if(type == "order")
      Execute(reply);
   else if(type == "lookup")
      Lookup(Field(reply, "id"));
  }

// All open positions on the account, by symbol, signed (for reconciliation on the platform).
string PositionsText()
  {
   string out = "";
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      double volume = PositionGetDouble(POSITION_VOLUME);
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL) volume = -volume;
      out += PositionGetString(POSITION_SYMBOL) + ":" + Num(volume, 2) + ";";
     }
   return(out);
  }

//+------------------------------------------------------------------+
//| Local checks: the terminal knows the real symbol rules            |
//+------------------------------------------------------------------+
bool CheckVolume(string symbol, double volume, string &why)
  {
   double vmin  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(volume < vmin - 1e-9) { why = "volume " + Num(volume, 2) + " is below the minimum " + Num(vmin, 2); return(false); }
   if(volume > vmax + 1e-9) { why = "volume " + Num(volume, 2) + " is above the maximum " + Num(vmax, 2); return(false); }
   if(vstep > 0 && MathAbs(volume / vstep - MathRound(volume / vstep)) > 1e-6)
     { why = "volume " + Num(volume, 2) + " is not a multiple of the step " + Num(vstep, 2); return(false); }
   if(MaxVolumePerOrder > 0 && volume > MaxVolumePerOrder + 1e-9)
     { why = "volume is above this terminal's safety cap of " + Num(MaxVolumePerOrder, 2); return(false); }
   return(true);
  }

bool CheckPrice(string symbol, double price, string &why)
  {
   if(price <= 0) return(true);
   double tick = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick > 0 && MathAbs(price / tick - MathRound(price / tick)) > 1e-6)
     { why = "price " + DoubleToString(price, 8) + " is not a multiple of the tick size"; return(false); }
   return(true);
  }

ENUM_ORDER_TYPE_FILLING Filling(string symbol)
  {
   long modes = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) return(ORDER_FILLING_FOK);
   if((modes & SYMBOL_FILLING_IOC) != 0) return(ORDER_FILLING_IOC);
   return(ORDER_FILLING_RETURN);
  }

ulong Deviation(string symbol, double slippage_pct, double price)
  {
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(slippage_pct > 0 && price > 0 && point > 0)
      return((ulong)MathMax(1, MathFloor(price * slippage_pct / 100.0 / point)));
   return((ulong)DefaultDeviationPts);
  }

//+------------------------------------------------------------------+
//| Executing an instruction                                          |
//+------------------------------------------------------------------+
void Execute(string reply)
  {
   string id       = Field(reply, "id");
   string intent   = Field(reply, "intent");
   string side     = Field(reply, "side");
   string symbol   = Field(reply, "symbol");
   string otype    = Field(reply, "order_type");
   double volume   = StringToDouble(Field(reply, "volume"));
   double price    = StringToDouble(Field(reply, "price"));
   double trigger  = StringToDouble(Field(reply, "trigger_price"));
   double sl       = StringToDouble(Field(reply, "sl"));
   double tp       = StringToDouble(Field(reply, "tp"));
   double refprice = StringToDouble(Field(reply, "reference_price"));
   double slip     = StringToDouble(Field(reply, "max_slippage_pct"));
   string why;

   if(!SymbolSelect(symbol, true))
     { Report(id, "rejected", "&message=" + UrlEncode("symbol " + symbol + " does not exist in this terminal")); return; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
     { Report(id, "rejected", "&message=" + UrlEncode("algo trading is disabled in the terminal")); return; }

   if(intent == "open")
     {
      if(!CheckVolume(symbol, volume, why) || !CheckPrice(symbol, price, why) || !CheckPrice(symbol, trigger, why)
         || !CheckPrice(symbol, sl, why) || !CheckPrice(symbol, tp, why))
        { Report(id, "rejected", "&message=" + UrlEncode(why)); return; }
      OpenOrder(id, side, symbol, otype, volume, price, trigger, sl, tp, refprice, slip);
     }
   else if(intent == "close")
      ClosePositions(id, symbol, side, volume, slip);
   else if(intent == "modify_sl" || intent == "modify_tp")
      ModifyStops(id, symbol, intent == "modify_sl" ? sl : -1, intent == "modify_tp" ? tp : -1);
   else if(intent == "cancel")
      CancelPending(id, symbol);
   else
      Report(id, "rejected", "&message=" + UrlEncode("unknown instruction " + intent));
  }

void SendAndReport(string id, MqlTradeRequest &request)
  {
   MqlTradeResult result;
   ZeroMemory(result);
   bool sent = OrderSend(request, result);
   uint rc = result.retcode;
   if(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL)
     {
      if(result.deal > 0 && result.volume > 0)
         Report(id, "filled", "&ticket=" + (string)result.order + "&deal=" + (string)result.deal
                + "&price=" + DoubleToString(result.price, 8) + "&volume=" + Num(result.volume, 2));
      else
         Report(id, "placed", "&ticket=" + (string)result.order);   // fill arrives via OnTradeTransaction
     }
   else if(rc == TRADE_RETCODE_PLACED)
      Report(id, "placed", "&ticket=" + (string)result.order);
   else if(!sent && (rc == 0 || rc == TRADE_RETCODE_TIMEOUT || rc == TRADE_RETCODE_CONNECTION))
      // We do not know whether the server executed it: the platform will ask us to look it up.
      Report(id, "error", "&message=" + UrlEncode("no definite answer from the trade server (" + (string)rc + ")"));
   else
      Report(id, "rejected", "&message=" + UrlEncode(result.comment + " (" + (string)rc + ")"));
  }

void OpenOrder(string id, string side, string symbol, string otype, double volume, double price, double trigger,
               double sl, double tp, double refprice, double slip)
  {
   MqlTradeRequest request;
   ZeroMemory(request);
   bool buy = (side == "BUY");
   request.symbol       = symbol;
   request.volume       = volume;
   request.sl           = sl;
   request.tp           = tp;
   request.magic        = (ulong)g_magic;
   request.comment      = id;
   request.type_filling = Filling(symbol);
   if(otype == "MARKET")
     {
      request.action    = TRADE_ACTION_DEAL;
      request.type      = buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      request.price     = buy ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID);
      request.deviation = Deviation(symbol, slip, refprice > 0 ? refprice : request.price);
     }
   else
     {
      request.action = TRADE_ACTION_PENDING;
      request.type_time = ORDER_TIME_GTC;
      if(otype == "LIMIT")      { request.type = buy ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT; request.price = price; }
      else if(otype == "STOP")  { request.type = buy ? ORDER_TYPE_BUY_STOP : ORDER_TYPE_SELL_STOP; request.price = trigger; }
      else                      { request.type = buy ? ORDER_TYPE_BUY_STOP_LIMIT : ORDER_TYPE_SELL_STOP_LIMIT;
                                  request.price = trigger; request.stoplimit = price; }
     }
   SendAndReport(id, request);
  }

// Close this platform's positions on the symbol, up to `volume`, oldest first.
void ClosePositions(string id, string symbol, string side, double volume, double slip)
  {
   double remaining = volume, filled = 0, value = 0;
   ulong last_deal = 0, last_order = 0;
   string problem = "";
   for(int i = 0; i < PositionsTotal() && remaining > 1e-9; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != symbol || PositionGetInteger(POSITION_MAGIC) != g_magic)
         continue;
      bool is_long = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      // `side` is the closing side: SELL closes longs, BUY closes shorts.
      if((side == "SELL" && !is_long) || (side == "BUY" && is_long)) continue;
      double size = MathMin(remaining, PositionGetDouble(POSITION_VOLUME));
      MqlTradeRequest request; MqlTradeResult result;
      ZeroMemory(request); ZeroMemory(result);
      request.action       = TRADE_ACTION_DEAL;
      request.position     = ticket;
      request.symbol       = symbol;
      request.volume       = size;
      request.type         = is_long ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      request.price        = is_long ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK);
      request.deviation    = Deviation(symbol, slip, request.price);
      request.magic        = (ulong)g_magic;
      request.comment      = id;
      request.type_filling = Filling(symbol);
      if(OrderSend(request, result) && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_DONE_PARTIAL))
        {
         filled += result.volume; value += result.volume * result.price; remaining -= result.volume;
         last_deal = result.deal; last_order = result.order;
        }
      else
        { problem = result.comment + " (" + (string)result.retcode + ")"; break; }
     }
   if(filled > 0)
      Report(id, "filled", "&ticket=" + (string)last_order + "&deal=" + (string)last_deal
             + "&price=" + DoubleToString(value / filled, 8) + "&volume=" + Num(filled, 2)
             + (problem != "" ? "&message=" + UrlEncode("partly closed; then " + problem) : ""));
   else if(problem != "")
      Report(id, "rejected", "&message=" + UrlEncode(problem));
   else
      Report(id, "cancelled", "&message=" + UrlEncode("no matching position in the terminal"));
  }

void ModifyStops(string id, string symbol, double sl, double tp)
  {
   int changed = 0;
   string problem = "";
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != symbol || PositionGetInteger(POSITION_MAGIC) != g_magic)
         continue;
      MqlTradeRequest request; MqlTradeResult result;
      ZeroMemory(request); ZeroMemory(result);
      request.action   = TRADE_ACTION_SLTP;
      request.position = ticket;
      request.symbol   = symbol;
      request.sl       = sl >= 0 ? sl : PositionGetDouble(POSITION_SL);
      request.tp       = tp >= 0 ? tp : PositionGetDouble(POSITION_TP);
      request.magic    = (ulong)g_magic;
      if(OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE) changed++;
      else problem = result.comment + " (" + (string)result.retcode + ")";
     }
   if(problem != "") Report(id, "rejected", "&message=" + UrlEncode(problem));
   else if(changed == 0) Report(id, "cancelled", "&message=" + UrlEncode("no matching position in the terminal"));
   else Report(id, "applied", "&message=" + UrlEncode("updated " + (string)changed + " position(s)"));
  }

void CancelPending(string id, string symbol)
  {
   int removed = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0 || OrderGetString(ORDER_SYMBOL) != symbol || OrderGetInteger(ORDER_MAGIC) != g_magic) continue;
      MqlTradeRequest request; MqlTradeResult result;
      ZeroMemory(request); ZeroMemory(result);
      request.action = TRADE_ACTION_REMOVE;
      request.order  = ticket;
      if(OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE) removed++;
     }
   Report(id, "applied", "&message=" + UrlEncode("removed " + (string)removed + " pending order(s)"));
  }

//+------------------------------------------------------------------+
//| Looking up an instruction whose result was lost                  |
//+------------------------------------------------------------------+
void Lookup(string id)
  {
   if(id == "") return;
   HistorySelect(TimeCurrent() - 7 * 86400, TimeCurrent() + 86400);
   double volume = 0, value = 0;
   ulong deal_id = 0, order_id = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      ulong order = (ulong)HistoryDealGetInteger(deal, DEAL_ORDER);
      bool ours = HistoryDealGetString(deal, DEAL_COMMENT) == id
                  || (HistoryOrderSelect(order) && HistoryOrderGetString(order, ORDER_COMMENT) == id);
      if(!ours) continue;
      double v = HistoryDealGetDouble(deal, DEAL_VOLUME);
      volume += v; value += v * HistoryDealGetDouble(deal, DEAL_PRICE);
      if(deal_id == 0) { deal_id = deal; order_id = order; }
     }
   if(volume > 0)
     {
      Report(id, "filled", "&ticket=" + (string)order_id + "&deal=" + (string)deal_id
             + "&price=" + DoubleToString(value / volume, 8) + "&volume=" + Num(volume, 2));
      return;
     }
   for(int i = 0; i < OrdersTotal(); i++)
     {
      ulong ticket = OrderGetTicket(i);
      if(ticket != 0 && OrderGetString(ORDER_COMMENT) == id)
        { Report(id, "placed", "&ticket=" + (string)ticket); return; }
     }
   Report(id, "not_found", "");
  }

//+------------------------------------------------------------------+
//| Fills of pending orders, reported as they happen                 |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != g_magic || g_magic == 0) return;
   ulong order = (ulong)HistoryDealGetInteger(trans.deal, DEAL_ORDER);
   string id = HistoryOrderSelect(order) ? HistoryOrderGetString(order, ORDER_COMMENT) : "";
   if(StringSubstr(id, 0, 2) != "TB") return;
   // The platform ignores a fill it has already recorded (same deal id).
   Report(id, "filled", "&ticket=" + (string)order + "&deal=" + (string)trans.deal
          + "&price=" + DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_PRICE), 8)
          + "&volume=" + Num(HistoryDealGetDouble(trans.deal, DEAL_VOLUME), 2));
  }
//+------------------------------------------------------------------+
