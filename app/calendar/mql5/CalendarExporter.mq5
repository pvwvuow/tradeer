//+------------------------------------------------------------------+
//| CalendarExporter.mq5                                             |
//| MT5 Trading Workstation: exports the MQL5 economic calendar so   |
//| the app can read it (the Python package has no calendar access). |
//|                                                                  |
//| Install: the app copies this file to <data folder>\MQL5\Services.|
//| Compile it in MetaEditor (F7), then in MT5 open Navigator >      |
//| Services, right-click CalendarExporter > Add service, and start  |
//| it. It writes Common\Files\tradeer_calendar.csv every 5 minutes. |
//|                                                                  |
//| Read-only: it never trades and never changes a chart.            |
//+------------------------------------------------------------------+
#property service
#property copyright "MT5 Trading Workstation"
#property version   "1.00"
#property description "Writes the economic calendar to Common\\Files\\tradeer_calendar.csv"

input int InpDaysBack        = 1;  // Days of past events to include
input int InpDaysAhead       = 7;  // Days of upcoming events to include
input int InpIntervalMinutes = 5;  // Minutes between exports

#define CSV_FILE  "tradeer_calendar.csv"
#define TEMP_FILE "tradeer_calendar.tmp"

//+------------------------------------------------------------------+
//| Service entry point: export, wait, repeat until stopped          |
//+------------------------------------------------------------------+
void OnStart()
  {
   PrintFormat("CalendarExporter started: writing Common\\Files\\%s every %d minutes",
               CSV_FILE, InpIntervalMinutes);
   while(!IsStopped())
     {
      ExportCalendar();
      for(int second = 0; second < InpIntervalMinutes * 60 && !IsStopped(); second++)
         Sleep(1000);
     }
   Print("CalendarExporter stopped");
  }

//+------------------------------------------------------------------+
//| Broker server time minus UTC, rounded to 15 minutes              |
//+------------------------------------------------------------------+
long ServerOffsetSeconds()
  {
   long offset = (long)(TimeTradeServer() - TimeGMT());
   return (long)(MathRound(offset / 900.0) * 900.0);
  }

//+------------------------------------------------------------------+
//| Importance as the app's impact words                             |
//+------------------------------------------------------------------+
string ImportanceText(const ENUM_CALENDAR_EVENT_IMPORTANCE importance)
  {
   switch(importance)
     {
      case CALENDAR_IMPORTANCE_HIGH:
         return "high";
      case CALENDAR_IMPORTANCE_MODERATE:
         return "medium";
      case CALENDAR_IMPORTANCE_LOW:
         return "low";
      default:
         return "none";
     }
  }

//+------------------------------------------------------------------+
//| A value with its unit, for example 2.5% or 254K                  |
//+------------------------------------------------------------------+
string ValueText(const bool has_value, const double value, const MqlCalendarEvent &event)
  {
   if(!has_value)
      return "";
   string text = DoubleToString(value, (int)event.digits);
   switch(event.multiplier)
     {
      case CALENDAR_MULTIPLIER_THOUSANDS:
         text += "K";
         break;
      case CALENDAR_MULTIPLIER_MILLIONS:
         text += "M";
         break;
      case CALENDAR_MULTIPLIER_BILLIONS:
         text += "B";
         break;
      case CALENDAR_MULTIPLIER_TRILLIONS:
         text += "T";
         break;
      default:
         break;
     }
   if(event.unit == CALENDAR_UNIT_PERCENT)
      text += "%";
   return text;
  }

//+------------------------------------------------------------------+
//| A CSV text field in double quotes                                |
//+------------------------------------------------------------------+
string Quoted(string text)
  {
   StringReplace(text, "\"", "\"\"");
   StringReplace(text, "\r", " ");
   StringReplace(text, "\n", " ");
   return "\"" + text + "\"";
  }

//+------------------------------------------------------------------+
//| Write text as UTF-8 to a binary file                             |
//+------------------------------------------------------------------+
bool WriteUtf8(const int handle, const string text)
  {
   uchar bytes[];
   int count = StringToCharArray(text, bytes, 0, WHOLE_ARRAY, CP_UTF8);
   if(count <= 1)
      return true;
   return FileWriteArray(handle, bytes, 0, count - 1) == (uint)(count - 1);
  }

//+------------------------------------------------------------------+
//| One export: write a temporary file, then replace the CSV         |
//+------------------------------------------------------------------+
bool ExportCalendar()
  {
   datetime now = TimeTradeServer();
   datetime from = now - InpDaysBack * 86400;
   datetime to = now + InpDaysAhead * 86400;
   MqlCalendarValue values[];
   ResetLastError();
   int count = CalendarValueHistory(values, from, to, NULL, NULL);
   if(count < 0)
     {
      PrintFormat("CalendarExporter: CalendarValueHistory failed, error %d", GetLastError());
      return false;
     }
   long offset = ServerOffsetSeconds();
   int handle = FileOpen(TEMP_FILE, FILE_WRITE | FILE_BIN | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("CalendarExporter: cannot write %s, error %d", TEMP_FILE, GetLastError());
      return false;
     }
   bool ok = WriteUtf8(handle,
                       "time_utc,currency,importance,event,actual,forecast,previous,country\r\n");
   int written = 0;
   for(int index = 0; index < count && ok; index++)
     {
      MqlCalendarEvent event;
      MqlCalendarCountry country;
      if(!CalendarEventById(values[index].event_id, event))
         continue;
      if(!CalendarCountryById(event.country_id, country))
         continue;
      string line = IntegerToString((long)values[index].time - offset) + ","
                    + country.currency + ","
                    + ImportanceText(event.importance) + ","
                    + Quoted(event.name) + ","
                    + Quoted(ValueText(values[index].HasActualValue(),
                                       values[index].GetActualValue(), event)) + ","
                    + Quoted(ValueText(values[index].HasForecastValue(),
                                       values[index].GetForecastValue(), event)) + ","
                    + Quoted(ValueText(values[index].HasPreviousValue(),
                                       values[index].GetPreviousValue(), event)) + ","
                    + country.code + "\r\n";
      ok = WriteUtf8(handle, line);
      written++;
     }
   FileClose(handle);
   if(!ok)
     {
      PrintFormat("CalendarExporter: writing %s failed, error %d", TEMP_FILE, GetLastError());
      return false;
     }
   if(!FileMove(TEMP_FILE, FILE_COMMON, CSV_FILE, FILE_COMMON | FILE_REWRITE))
     {
      PrintFormat("CalendarExporter: cannot replace %s, error %d", CSV_FILE, GetLastError());
      return false;
     }
   static int last_written = -1;
   if(written != last_written)
      PrintFormat("CalendarExporter: %d events written to Common\\Files\\%s", written, CSV_FILE);
   last_written = written;
   return true;
  }
//+------------------------------------------------------------------+
