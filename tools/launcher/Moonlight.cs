// Moonlight.exe: opens the bot GUI with no console window.
// Build with tools\launcher\build.bat. It runs "pythonw -m moonlight" from
// the folder that holds the exe (the repo root), passing its arguments on.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;

static class Launcher
{
    static void Main(string[] args)
    {
        string repoRoot = AppDomain.CurrentDomain.BaseDirectory;
        string passedArguments = string.Join(" ", args.Select(a => "\"" + a + "\""));
        var start = new ProcessStartInfo("pythonw.exe", "-m moonlight " + passedArguments)
        {
            WorkingDirectory = repoRoot,
            UseShellExecute = false,
        };
        try
        {
            Process.Start(start);
        }
        catch (Win32Exception)
        {
            MessageBox.Show(
                "pythonw.exe was not found on PATH. Install Python 3.13 with " +
                "\"Add python.exe to PATH\" ticked, then run pip install -e . in\n" + repoRoot,
                "Moonlight", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
