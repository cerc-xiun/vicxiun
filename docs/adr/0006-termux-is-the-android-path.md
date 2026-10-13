# Termux is the Android path

Android support means Termux: the tool is installed and run inside that
terminal environment as an ordinary command-line program, and its finished
files are placed in the phone's shared storage so the gallery and file manager
can see them. Termux is the one place on Android where a real Python
environment and the helper programs the tool depends on can run together, so
treating it as the Android path is what lets the same engine serve both
platforms; the system's habit of interrupting long work is warned about rather
than hidden.

**_Rejected_**: a native Android application, or a graphical interface. Both
are parked rather than ruled out forever, but either would pull the project
toward an app-shaped distribution and a second implementation, which is the
duplication the project deliberately avoids.
