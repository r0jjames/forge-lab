package lab.deprovisioncluster;

import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class DeprovisionClusterSpecTest {
    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new DeprovisionClusterSpec().plan());
    }
}
