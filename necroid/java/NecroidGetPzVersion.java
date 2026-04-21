// Probe: read PZ's own Core.gameVersion + Core.buildVersion via reflection and
// print "MAJOR.MINOR[SUFFIX].BUILD" (e.g. "41.78.19"). Invoked by
// necroid.pzversion.detect_pz_version() with -cp pointing at the target PZ
// install's content dir.
public class NecroidGetPzVersion {
    public static void main(String[] args) throws Exception {
        Class<?> core = Class.forName("zombie.core.Core");
        java.lang.reflect.Field gvf = core.getDeclaredField("gameVersion");
        gvf.setAccessible(true);
        Object gv = gvf.get(null);               // zombie.core.GameVersion
        java.lang.reflect.Field bvf = core.getDeclaredField("buildVersion");
        bvf.setAccessible(true);
        int bv = bvf.getInt(null);
        System.out.println(gv.toString() + "." + bv);
    }
}
