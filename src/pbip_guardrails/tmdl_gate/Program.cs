using System.Globalization;
using Microsoft.AnalysisServices.Tabular;

// Parses a TMDL folder with Microsoft's own serializer (the same code Power BI uses).
// A malformed TMDL doesn't break one visual: it stops Desktop from opening the whole
// project, and it can't be seen by reading the diff.
if (args.Length < 1) {
    Console.Error.WriteLine("usage: tmdl-gate <path>/X.SemanticModel/definition");
    return 2;
}

// Parser messages follow the OS locale; pin them to English so output is stable.
CultureInfo.DefaultThreadCurrentUICulture = CultureInfo.CurrentUICulture = new CultureInfo("en-US");

try {
    var m = TmdlSerializer.DeserializeModelFromFolder(args[0]);
    Console.WriteLine($"OK  tables={m.Tables.Count}  measures={m.Tables.Sum(t => t.Measures.Count)}");
    return 0;
} catch (Exception ex) {
    Console.Error.WriteLine($"FAIL  {ex.GetType().Name}");
    // The detail (document and line) comes in the following lines of the message:
    // printing it is the difference between "it won't open" and knowing what to fix.
    foreach (var line in ex.Message.Split('\n').Take(12))
        Console.Error.WriteLine($"       {line.TrimEnd()}");
    if (ex.InnerException is not null)
        Console.Error.WriteLine($"       cause: {ex.InnerException.Message.Split('\n')[0]}");
    return 1;
}
