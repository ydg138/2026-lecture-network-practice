### Task 1: Build Your Own Iterative Resolver

- **Why did the root server not simply hand you the address?** 루트 서버는 모든 도메인의 실제 IP 주소를 직접 가지고 있는 것이 아니라, 각 최상위 도메인(TLD)을 담당하는 DNS 서버의 위치와 위임 정보를 알려주는 역할을 하기 때문이다. 따라서 리졸버는 루트 서버에 질의한 후, 전달받은 TLD 서버로 이동하고, 다시 해당 도메인의 authoritative DNS 서버를 찾아가는 과정을 수행해야 한다.
- **What did you do when a delegation arrived without glue?** 위임 정보에 NS 이름은 있지만 해당 네임서버의 IP 주소인 glue record가 함께 제공되지 않는 경우, 바로 해당 서버에 질의할 수 없었다. 따라서 해당 네임서버의 호스트 이름을 먼저 다시 조회하여 IP 주소를 얻은 뒤, 그 주소를 이용해 다음 단계의 DNS 질의를 수행하도록 처리했다. 이렇게 하면 glue가 없는 경우에도 DNS 계층 구조를 따라가며 최종적으로 authoritative DNS 서버에 도달할 수 있다.
- **How many servers did you end up asking?** 일반적으로 노트북은 설정된 재귀 DNS 서버에 한 번 질의하고, 나머지 DNS 계층 탐색은 해당 DNS 서버가 대신 수행한다. 반면 이번 과제에서 구현한 iterative resolver는 루트 서버부터 TLD 서버와 authoritative DNS 서버까지 직접 질의한다.

실행 결과에서 확인된 hop 수는 www.korea.ac.kr 3회, dns.google 3회, en.wikipedia.org 6회, www.stanford.edu 6회, www.microsoft.com 10회였으며, 5개 이름에 대한 평균 hop 수는 약 5.6회였다.

### Task 2: On the Wire, and Does DNS Really Steer You?

- **Delegation response와 answer response의 차이 (캡처에서 본 것)** 두 응답은 완전히 같은 DNS 패킷 형식이고, 어느 섹션이 채워졌는지만 다르다. dns.pcapng의 4번 패킷(delegation)은 Answer RRs가 0이고 Authority에 korea.ac.kr.의 NS 2개, Additional에 그 glue A 레코드 2개가 들어 있다. 반면 6번 패킷(answer)은 Answer 섹션에 `www.korea.ac.kr A 163.152.6.10`이 있고 AA 플래그가 1이다. 가장 큰 응답은 2번 패킷(383 bytes)으로, 루트 서버가 kr.로 위임하면서 NS 6개와 glue 10개(A 6, AAAA 4)를 함께 보냈기 때문에 커졌다.
- **Third-party 판별 규칙과 틀린 사이트** 규칙: "CNAME 체인의 마지막 zone(eTLD+1)이 사이트 자신의 조직이 아닌, 알려진 CDN 사업자(Akamai, Fastly, CloudFront 등)의 zone이면 third party". 이 규칙은 www.stanford.edu를 틀렸다. 체인이 `netlifyglobalcdn.com`(Netlify CDN)에서 끝나지만 내 CDN 목록에 없어서 "own"으로 판정했다. 즉 목록 기반 규칙은 모르는 CDN을 놓친다. 참고로 끝 두 라벨만 비교하는 단순 규칙은 www.wikipedia.org를 틀린다(`wikimedia.org`는 같은 조직인데 third party로 판정). 또한 CNAME 없이 anycast로 운영되는 CDN은 어떤 CNAME 기반 규칙으로도 잡을 수 없다.
- **Steering number와 claim (b)** wifi와 tethering 두 네트워크, 세 리졸버(system, google, quad9)로 측정한 결과 **CDN 사이트 7개 중 7개**가 리졸버나 네트워크에 따라 다른 주소를 받았다. Akamai 사이트(microsoft, adobe, apple)는 Quad9에서만 주소가 달랐는데, Google은 EDNS Client Subnet으로 사용자 위치를 전달하지만 Quad9은 전달하지 않기 때문으로 보인다. Fastly 사이트(cnn, bbc, spotify, nytimes)는 학교 리졸버에서만 다른 주소(146.75.x.x)가 나왔다. 이 결과는 claim (b)를 뒷받침하지만, 받은 서버가 실제로 "더 가까운지"는 RTT를 재지 않았으므로 증명하지 못했다. 또 Stanford(Netlify)는 모든 곳에서 같은 주소를 받았는데, 이는 DNS가 아니라 anycast로 가까운 서버에 연결하는 방식이기 때문이다.

### Task 3: Beat the Baseline Cache

- **Baseline의 두 가지 문제 (원인은 같다)** Baseline은 upstream이 준 TTL을 버리고 모든 레코드를 고정 60초 동안 보관한다. (1) **정확성 문제**: TTL이 60초보다 짧은 레코드(www.microsoft.com 20초, www.cnn.com 30초)를 만료 후에도 내준다 → stale 266개. (2) **성능 문제**: TTL이 60초보다 긴 레코드(최대 86400초)를 아직 유효한데도 60초마다 버리고 다시 조회한다. 예를 들어 www.korea.ac.kr(TTL 3600초)은 1번이면 충분한데 25번 조회했다. 내 캐시는 레코드마다 `저장 시각 + TTL`까지만 보관해서 stale 0, upstream 275번(baseline 대비 15% 감소)을 달성했다.
- **Floor: 275번** 올바른 캐시는 유효한 레코드가 없을 때 반드시 upstream에 물어야 한다. 이런 경우는 (a) 이름을 처음 조회할 때(10개 이름 → 10번), (b) 마지막으로 받은 레코드의 TTL이 지난 뒤 조회가 들어올 때(265번)다. 조회가 들어온 그 순간에 받아 오는 것이 TTL을 가장 늦게까지 쓰는 방법이고, 미리 받아 두면(prefetch) 오히려 upstream 조회가 늘어난다. 그래서 어떤 자료구조를 써도 10 + 265 = 275보다 줄일 수 없고, 내 캐시가 이미 이 하한에 도달했다. 더 줄이려면 TTL을 어겨야 하는데, 그게 바로 baseline이 stale을 낸 원인이다.
- **Baseline이 가장 못 다루는 레코드: www.microsoft.com** TTL이 20초로 가장 짧고, Zipf 분포상 가장 인기 있는 이름이라(1000번 중 322번) 60초 고정 보관의 피해가 가장 크다. stale 266개 중 189개가 이 이름에서 나왔다.