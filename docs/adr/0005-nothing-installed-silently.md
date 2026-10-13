# Nothing is installed without the user's knowledge

When a Prerequisite is missing, the tool names it, prints the exact command
that installs it and stops; it never runs an installer, never asks for
administrator rights, never edits PATH and never reads or writes the Windows
registry. Installing a program on someone's machine on their behalf is the
largest thing a tool like this can do without being asked, and a tool with no
ability to change the machine around it is one a careful person can try
safely.

**_Rejected_**: automatically installing missing Prerequisites. It would remove
the tool's most common point of friction, which is exactly why the answer needs
writing down: the promise that installing vicxiun cannot damage the machine
outranks the convenience.
