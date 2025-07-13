# AdGuard Home Helm Chart

[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/adguardhome)](https://artifacthub.io/packages/helm/adguardhome/adguardhome)
[![GitLab Pipeline](https://gitlab.trofimoff.net/charts/adguardhome/badges/main/pipeline.svg)](https://gitlab.trofimoff.net/charts/adguardhome/-/pipelines)
[![GitLab Release](https://img.shields.io/badge/dynamic/yaml?color=blue&label=release&query=$.version&url=https://gitlab.trofimoff.net/charts/adguardhome/-/raw/main/Chart.yaml)](https://gitlab.trofimoff.net/charts/adguardhome/-/releases)

Helm chart for deploying [AdGuard Home](https://adguard.com/en/adguard-home/overview.html) DNS server.

## Installation

```bash
helm repo add adguardhome https://gitlab.trofimoff.net/api/v4/projects/32/packages/helm/stable
helm repo update
helm install my-adguardhome adguardhome/adguardhome --namespace adguardhome --create-namespace
```

## Configuration

### Replica Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas | `1` |

### Image Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `image.repository` | Image repository | `adguard/adguardhome` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `image.tag` | Image tag | `""` |

### Service Account Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `serviceAccount.create` | Create service account | `true` |
| `serviceAccount.automount` | Automount API credentials | `true` |
| `serviceAccount.annotations` | ServiceAccount annotations | `{}` |
| `serviceAccount.name` | ServiceAccount name | `""` |

### Pod Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `podAnnotations` | Pod annotations | `{}` |
| `podLabels` | Pod labels | `{}` |
| `podSecurityContext` | Pod security context | `{}` |
| `securityContext` | Container security context | `{}` |

### Service Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `service.type` | Service type | `ClusterIP` |
| `service.externalIPs` | External IPs | `[]` |
| `service.ports` | Service ports | See below |

#### Default Ports:
- `http-setup`: 3000 TCP
- `dns-udp`: 53 UDP
- `dns-tcp`: 53 TCP
- `http`: 80 TCP

#### Optional Ports:
```yaml
# - name: https-doh
#   containerPort: 443
#   protocol: TCP
# - name: doh-udp
#   containerPort: 443
#   protocol: UDP
# - name: dot
#   containerPort: 853
#   protocol: TCP
# - name: doq-1
#   containerPort: 784 
#   protocol: UDP
# - name: doq-2
#   containerPort: 853 
#   protocol: UDP
# - name: doq-3
#   containerPort: 8853
#   protocol: UDP
# - name: dnscrypt-tcp
#   containerPort: 5443
#   protocol: TCP
# - name: dnscrypt-udp
#   containerPort: 5443
#   protocol: UDP
```

### Ingress Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `ingress.enabled` | Enable ingress | `false` |
| `ingress.className` | Ingress class name | `""` |
| `ingress.annotations` | Ingress annotations | `{}` |
| `ingress.hosts` | Ingress hosts | See below |
| `ingress.tls` | Ingress TLS | `[]` |

#### Hosts Configuration:
```yaml
hosts:
  - host: chart-example.local
    paths:
      - path: /
        pathType: ImplementationSpecific
        backend:
          service:
            port:
              name: http
```

#### TLS Configuration:
```yaml
tls:
  - secretName: chart-example-tls
    hosts:
      - chart-example.local
```

### Resource Configuration
```yaml
resources: {}
  # limits:
  #   cpu: 100m
  #   memory: 128Mi
  # requests:
  #   cpu: 100m
  #   memory: 128Mi
```

### Probes Configuration
```yaml
livenessProbe:
  httpGet:
    path: /
    port: http
  initialDelaySeconds: 300
  timeoutSeconds: 1
  periodSeconds: 15

readinessProbe:
  httpGet:
    path: /
    port: http
```

### Autoscaling Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `autoscaling.enabled` | Enable autoscaling | `false` |
| `autoscaling.minReplicas` | Min replicas | `1` |
| `autoscaling.maxReplicas` | Max replicas | `100` |
| `autoscaling.targetCPUUtilizationPercentage` | CPU target | `80` |

### Persistence Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `persistence.enabled` | Enable persistence | `true` |
| `persistence.storageClass` | Storage class | `""` |
| `persistence.accessMode` | Access mode | `ReadWriteOnce` |
| `persistence.size` | Storage size | `8Gi` |
| `persistence.annotations` | PVC annotations | `{}` |

### Additional Configuration
| Parameter | Description | Default |
|-----------|-------------|---------|
| `imagePullSecrets` | Image pull secrets | `[]` |
| `nameOverride` | Name override | `""` |
| `fullnameOverride` | Fullname override | `""` |
| `volumes` | Additional volumes | `[]` |
| `volumeMounts` | Additional mounts | `[]` |
| `nodeSelector` | Node selector | `{}` |
| `tolerations` | Tolerations | `[]` |
| `affinity` | Affinity rules | `{}` |

## Upgrading
```bash
helm upgrade my-adguardhome adguardhome/adguardhome -n adguardhome
```

## Uninstalling
```bash
helm uninstall my-adguardhome -n adguardhome
```

## Requirements
- Kubernetes 1.19+
- Helm 3.2.0+
- PV provisioner support

## Values Reference
Complete configuration options available in [values.yaml](values.yaml)