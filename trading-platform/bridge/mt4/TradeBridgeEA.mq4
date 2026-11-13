//+------------------------------------------------------------------+
//| TradeBridgeEA.mq4                                                |
//| MetaTrader 4 version of the trading platform's terminal bridge.  |
//|                                                                  |
//| Same protocol as the MT5 version: authenticate with a device     |
//| token, poll for work, check it against this terminal's symbol    |
//| rules, execute it, report exactly what happened. Your broker     |
//| password never leaves this terminal.                              |
//|                                                                  |
//| Setup: see docs/metatrader-bridge.md                             |
//+------------------------------------------------------------------+
#property copyright "Trading platform"
#property version   "1.00"
#property strict

input string ApiUrl              = "https://your-domain.example/api/v1/bridge"; // Platform bridge address
input string DeviceToken         = "";    // Device token shown once when you paired this account
input int    PollMilliseconds    = 1000;  // How often to ask for work
input int    DefaultSlippagePts  = 20;    // Allowed slippage (points) when the alert sets no limit
input double MaxVolumePerOrder   = 0.0;   // Local safety cap in lots (0 = no cap)

int g_magic = 0;

int OnInit()
  {
   if(StringLen(DeviceToken) < 10)
     {
      Print("TradeBridge: set the DeviceToken input (shown when you paired this account).");
      return(INIT_PARAMETERS_INCORRECT);
     }
   EventSetMillisecondTimer(MathMax(PollMilliseconds, 250));
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

//--- HTTP --------------------------------------------------------------
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
      if(err == 4060 || err == 4014)
         Print("TradeBridge: add ", ApiUrl, " to Tools > Options > Expert Advisors > Allow WebRequest.");
      return(-1);
     }
   reply = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return(status);
  }

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
   for(int attempt = 0; attempt < 5; attempt++)
     {
      int code = Post("/result", "id=" + UrlEncode(id) + "&status=" + status + extra, reply);
      if(code >= 200 && code < 500) return;
      Sleep(1000 * (attempt + 1));
     }
   Print("TradeBridge: could not report ", id, "; the platform will ask this terminal about it.");
  }

string Num(double value, int digits) { return(DoubleToString(value, digits)); }

//--- main loop -----------------------------------------------------------
void OnTimer()
  {
   string form = "login=" + IntegerToString(AccountNumber())
               + "&server=" + UrlEncode(AccountServer())
               + "&currency=" + UrlEncode(AccountCurrency())
               + "&balance=" + Num(AccountBalance(), 2)
               + "&equity=" + Num(AccountEquity(), 2)
               + "&margin_free=" + Num(AccountFreeMargin(), 2)
               + "&terminal=" + IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))
               + "&positions=" + UrlEncode(PositionsText());
   string reply;
   if(Post("/poll", form, reply) != 200) return;
   string magic = Field(reply, "magic");
   if(magic != "") g_magic = (int)StringToInteger(magic);
   string type = Field(reply, "type");
   if(type == "order") Execute(reply);
   else if(type == "lookup") Lookup(Field(reply, "id"));
  }

string PositionsText()
  {
   string out = "";
   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderType() == OP_BUY) out += OrderSymbol() + ":" + Num(OrderLots(), 2) + ";";
      else if(OrderType() == OP_SELL) out += OrderSymbol() + ":" + Num(-OrderLots(), 2) + ";";
     }
   return(out);
  }

//--- local checks ----------------------------------------------------------
bool CheckVolume(string symbol, double volume, string &why)
  {
   double vmin = MarketInfo(symbol, MODE_MINLOT), vmax = MarketInfo(symbol, MODE_MAXLOT), vstep = MarketInfo(symbol, MODE_LOTSTEP);
   if(volume < vmin - 1e-9) { why = "volume is below the minimum " + Num(vmin, 2); return(false); }
   if(volume > vmax + 1e-9) { why = "volume is above the maximum " + Num(vmax, 2); return(false); }
   if(vstep > 0 && MathAbs(volume / vstep - MathRound(volume / vstep)) > 1e-6) { why = "volume is not a multiple of the step " + Num(vstep, 2); return(false); }
   if(MaxVolumePerOrder > 0 && volume > MaxVolumePerOrder + 1e-9) { why = "volume is above this terminal's safety cap"; return(false); }
   return(true);
  }

int Slippage(string symbol, double slip_pct, double price)
  {
   double point = MarketInfo(symbol, MODE_POINT);
   if(slip_pct > 0 && price > 0 && point > 0) return((int)MathMax(1, MathFloor(price * slip_pct / 100.0 / point)));
   return(DefaultSlippagePts);
  }

string ErrorText(int code) { return("MT4 error " + IntegerToString(code)); }

// Errors after which the order may or may not have reached the trade server.
bool Ambiguous(int code) { return(code == 128 || code == 142 || code == 143 || code == 6 || code == 4); }

//--- executing ---------------------------------------------------------------
void Execute(string reply)
  {
   string id = Field(reply, "id"), intent = Field(reply, "intent"), side = Field(reply, "side");
   string symbol = Field(reply, "symbol"), otype = Field(reply, "order_type");
   double volume = StringToDouble(Field(reply, "volume")), price = StringToDouble(Field(reply, "price"));
   double trigger = StringToDouble(Field(reply, "trigger_price"));
   double sl = StringToDouble(Field(reply, "sl")), tp = StringToDouble(Field(reply, "tp"));
   double refprice = StringToDouble(Field(reply, "reference_price")), slip = StringToDouble(Field(reply, "max_slippage_pct"));
   string why;

   if(MarketInfo(symbol, MODE_POINT) <= 0)
     { Report(id, "rejected", "&message=" + UrlEncode("symbol " + symbol + " does not exist in this terminal")); return; }
   if(!IsTradeAllowed())
     { Report(id, "rejected", "&message=" + UrlEncode("trading is not allowed in the terminal")); return; }

   if(intent == "open")
     {
      if(!CheckVolume(symbol, volume, why)) { Report(id, "rejected", "&message=" + UrlEncode(why)); return; }
      if(otype == "STOP_LIMIT") { Report(id, "rejected", "&message=" + UrlEncode("MT4 has no stop-limit orders")); return; }
      bool buy = (side == "BUY");
      int cmd; double at;
      RefreshRates();
      if(otype == "MARKET") { cmd = buy ? OP_BUY : OP_SELL; at = buy ? MarketInfo(symbol, MODE_ASK) : MarketInfo(symbol, MODE_BID); }
      else if(otype == "LIMIT") { cmd = buy ? OP_BUYLIMIT : OP_SELLLIMIT; at = price; }
      else { cmd = buy ? OP_BUYSTOP : OP_SELLSTOP; at = trigger; }
      ResetLastError();
      int ticket = OrderSend(symbol, cmd, volume, at, Slippage(symbol, slip, refprice > 0 ? refprice : at), sl, tp, id, g_magic);
      if(ticket < 0)
        {
         int err = GetLastError();
         if(Ambiguous(err)) Report(id, "error", "&message=" + UrlEncode(ErrorText(err)));
         else Report(id, "rejected", "&message=" + UrlEncode(ErrorText(err)));
         return;
        }
      if(otype == "MARKET" && OrderSelect(ticket, SELECT_BY_TICKET))
         Report(id, "filled", "&ticket=" + IntegerToString(ticket) + "&deal=" + IntegerToString(ticket)
                + "&price=" + DoubleToString(OrderOpenPrice(), 8) + "&volume=" + Num(OrderLots(), 2));
      else
         Report(id, "placed", "&ticket=" + IntegerToString(ticket));
     }
   else if(intent == "close") ClosePositions(id, symbol, side, volume, slip);
   else if(intent == "modify_sl" || intent == "modify_tp") ModifyStops(id, symbol, intent == "modify_sl" ? sl : -1, intent == "modify_tp" ? tp : -1);
   else if(intent == "cancel") CancelPending(id, symbol);
   else Report(id, "rejected", "&message=" + UrlEncode("unknown instruction " + intent));
  }

void ClosePositions(string id, string symbol, string side, double volume, double slip)
  {
   double remaining = volume, filled = 0, value = 0;
   int last = 0;
   string problem = "";
   for(int i = 0; i < OrdersTotal() && remaining > 1e-9; i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol || OrderMagicNumber() != g_magic) continue;
      bool is_long = OrderType() == OP_BUY;
      if(OrderType() > OP_SELL) continue;
      if((side == "SELL" && !is_long) || (side == "BUY" && is_long)) continue;
      double size = MathMin(remaining, OrderLots());
      int ticket = OrderTicket();
      RefreshRates();
      double at = is_long ? MarketInfo(symbol, MODE_BID) : MarketInfo(symbol, MODE_ASK);
      if(OrderClose(ticket, size, at, Slippage(symbol, slip, at)))
        {
         filled += size; value += size * at; remaining -= size; last = ticket; i--;
        }
      else { problem = ErrorText(GetLastError()); break; }
     }
   if(filled > 0)
      Report(id, "filled", "&ticket=" + IntegerToString(last) + "&deal=" + id + "-" + IntegerToString(last)
             + "&price=" + DoubleToString(value / filled, 8) + "&volume=" + Num(filled, 2));
   else if(problem != "") Report(id, "rejected", "&message=" + UrlEncode(problem));
   else Report(id, "cancelled", "&message=" + UrlEncode("no matching position in the terminal"));
  }

void ModifyStops(string id, string symbol, double sl, double tp)
  {
   int changed = 0;
   string problem = "";
   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol || OrderMagicNumber() != g_magic || OrderType() > OP_SELL) continue;
      double new_sl = sl >= 0 ? sl : OrderStopLoss(), new_tp = tp >= 0 ? tp : OrderTakeProfit();
      if(OrderModify(OrderTicket(), OrderOpenPrice(), new_sl, new_tp, 0)) changed++;
      else problem = ErrorText(GetLastError());
     }
   if(problem != "") Report(id, "rejected", "&message=" + UrlEncode(problem));
   else if(changed == 0) Report(id, "cancelled", "&message=" + UrlEncode("no matching position in the terminal"));
   else Report(id, "applied", "&message=" + UrlEncode("updated " + IntegerToString(changed) + " position(s)"));
  }

void CancelPending(string id, string symbol)
  {
   int removed = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != symbol || OrderMagicNumber() != g_magic || OrderType() <= OP_SELL) continue;
      if(OrderDelete(OrderTicket())) removed++;
     }
   Report(id, "applied", "&message=" + UrlEncode("removed " + IntegerToString(removed) + " pending order(s)"));
  }

//--- looking up an instruction whose result was lost --------------------------
void Lookup(string id)
  {
   if(id == "") return;
   for(int pool = 0; pool < 2; pool++)
     {
      int total = pool == 0 ? OrdersTotal() : OrdersHistoryTotal();
      for(int i = total - 1; i >= 0; i--)
        {
         if(!OrderSelect(i, SELECT_BY_POS, pool == 0 ? MODE_TRADES : MODE_HISTORY)) continue;
         if(OrderComment() != id) continue;
         if(OrderType() <= OP_SELL)
            Report(id, "filled", "&ticket=" + IntegerToString(OrderTicket()) + "&deal=" + IntegerToString(OrderTicket())
                   + "&price=" + DoubleToString(OrderOpenPrice(), 8) + "&volume=" + Num(OrderLots(), 2));
         else if(pool == 0)
            Report(id, "placed", "&ticket=" + IntegerToString(OrderTicket()));
         else
            Report(id, "cancelled", "&message=" + UrlEncode("pending order was deleted or expired"));
         return;
        }
     }
   Report(id, "not_found", "");
  }
//+------------------------------------------------------------------+
