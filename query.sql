SELECT count(*)
FROM ProcessSample 
WHERE processDisplayName = 'haproxy' 
FACET entityName, aws.ec2.privateIpAddress, aws.arn, entityGuid
