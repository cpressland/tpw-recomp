/* tpw, the program: the host's tpw_main (main.c). Theme Park World.app loads
 * the same game as libtpw.dylib and enters it the same way (app/launcher.m). */
int tpw_main(int argc, char **argv);

int main(int argc, char **argv) { return tpw_main(argc, argv); }
