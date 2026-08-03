package lab.shared;

/**
 * Values every spec in this repo needs. Lives in its own package so no plan has
 * to reach into another plan's class for them (see plans/README.md, rule 4).
 */
public final class SpecConstants {

    /** Bamboo server reached over the `make ui` port-forward at publish time. */
    public static final String BAMBOO_URL = "http://localhost:8085";

    /**
     * Linked repository the forge-lab plans check out; created once in
     * Administration > Linked repositories.
     */
    public static final String REPO_NAME = "forge-lab";

    private SpecConstants() {
    }
}
