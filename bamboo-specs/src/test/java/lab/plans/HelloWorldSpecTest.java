package lab.plans;

import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import org.junit.Test;

public class HelloWorldSpecTest {
    @Test
    public void planIsValid() {
        Plan plan = new HelloWorldSpec().plan();
        EntityPropertiesBuilders.build(plan); // throws on invalid spec
    }
}
