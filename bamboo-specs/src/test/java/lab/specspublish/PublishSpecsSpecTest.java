package lab.specspublish;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import lab.shared.SpecConstants;
import org.junit.Test;

public class PublishSpecsSpecTest {

    @Test
    public void planIsOfflineValid() {
        // Throws if the plan is structurally invalid — offline validation.
        EntityPropertiesBuilders.build(new PublishSpecsSpec().plan());
    }

    @Test
    public void publishRunsOnTheHostAgent() {
        // Only the host reaches localhost:8085 through the `make ui`
        // port-forward, and only the host has ~/.forgelab/bamboo_pat.
        PlanProperties plan = EntityPropertiesBuilders.build(new PublishSpecsSpec().plan());
        assertEquals("Publish", plan.getStages().get(0).getName());
        assertTrue(
                "the publish job must require agent.role=host",
                plan.getStages().get(0).getJobs().get(0).getRequirements().stream()
                        .anyMatch(r -> "agent.role".equals(r.getKey())
                                && "host".equals(r.getMatchValue())));
    }

    @Test
    public void planPollsTheRepository() {
        // Without a trigger the plan publishes nothing and the drift this
        // feature exists to kill comes straight back.
        PlanProperties plan = EntityPropertiesBuilders.build(new PublishSpecsSpec().plan());
        assertTrue(
                "the plan must carry a repository polling trigger",
                plan.getTriggers().stream()
                        .anyMatch(t -> t.getClass().getSimpleName().startsWith("RepositoryPolling")));
    }

    @Test
    public void bambooUrlMatchesThePublisher() throws Exception {
        // publish_specs.py probes this URL before publishing to it. Drift here
        // makes the hook skip silently against a server that is actually up.
        Path publisher = Path.of(
                "src/main/java/lab/specspublish/scripts/publish_specs.py");
        List<String> lines = Files.readAllLines(publisher);
        assertTrue(
                "publish_specs.BAMBOO_URL must equal SpecConstants.BAMBOO_URL",
                lines.contains("BAMBOO_URL = \"" + SpecConstants.BAMBOO_URL + "\""));
    }
}
