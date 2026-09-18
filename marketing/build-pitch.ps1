param(
    [string]$OutputDirectory = $PSScriptRoot,
    [string]$TemplatePath = 'C:\Users\deepakbalan\Downloads\August Team Meeting.pptx'
)

$ErrorActionPreference = 'Stop'
$script:W = 13.333 * 72
$script:H = 7.5 * 72
$script:navy = 0x261407
$script:navy2 = 0xF8F4EF
$script:blue = 0xB87500
$script:gold = 0x0090E0
$script:green = 0x8A7A00
$script:white = 0x261407
$script:paper = 0xFFFFFF
$script:muted = 0x66594D
$script:card = 0xFFFFFF
$script:border = 0xDFC7AC
$script:red = 0x3030C0
$script:coverLayout = $null
$script:contentLayout = $null

function Remove-LayoutPlaceholders($slide) {
    for ($i = $slide.Shapes.Count; $i -ge 1; $i--) {
        $shape = $slide.Shapes.Item($i)
        if ($shape.Type -eq 14) { $shape.Delete() }
    }
}

function Add-Text($slide, $text, $x, $y, $w, $h, $size, $color, $bold=$false, $align=1, $font='Aptos') {
    $shape = $slide.Shapes.AddTextbox(1, $x, $y, $w, $h)
    $shape.TextFrame2.MarginLeft = 0
    $shape.TextFrame2.MarginRight = 0
    $shape.TextFrame2.MarginTop = 0
    $shape.TextFrame2.MarginBottom = 0
    $shape.TextFrame2.WordWrap = -1
    $shape.TextFrame2.TextRange.Text = $text
    $shape.TextFrame2.TextRange.Font.Name = $font
    $shape.TextFrame2.TextRange.Font.Size = $size
    $shape.TextFrame2.TextRange.Font.Fill.ForeColor.RGB = $color
    $shape.TextFrame2.TextRange.Font.Bold = $(if ($bold) {-1} else {0})
    $shape.TextFrame2.TextRange.ParagraphFormat.Alignment = $align
    return $shape
}

function Add-Box($slide, $x, $y, $w, $h, $fill=$script:card, $radius=$true, $line=$script:border) {
    $kind = $(if ($radius) {5} else {1})
    $shape = $slide.Shapes.AddShape($kind, $x, $y, $w, $h)
    $shape.Fill.ForeColor.RGB = $fill
    $shape.Fill.Transparency = 0
    $shape.Line.ForeColor.RGB = $line
    $shape.Line.Weight = 1
    return $shape
}

function Add-Line($slide, $x1, $y1, $x2, $y2, $color=$script:blue, $weight=2) {
    $line = $slide.Shapes.AddLine($x1, $y1, $x2, $y2)
    $line.Line.ForeColor.RGB = $color
    $line.Line.Weight = $weight
    return $line
}

function Add-Arrow($slide, $x1, $y1, $x2, $y2, $color=$script:blue, $weight=2) {
    $line = Add-Line $slide $x1 $y1 $x2 $y2 $color $weight
    $line.Line.EndArrowheadStyle = 3
    return $line
}

function Add-Base($presentation, $title, $section='AUTARCH / GOVERNED AGENT FACTORY') {
    $slide = $presentation.Slides.AddSlide($presentation.Slides.Count + 1, $script:contentLayout)
    Remove-LayoutPlaceholders $slide
    $slide.FollowMasterBackground = 0
    Add-Text $slide $section 48 17 650 15 9 $script:blue $true | Out-Null
    Add-Text $slide $title 48 38 850 48 28 $script:white $true | Out-Null
    Add-Text $slide ("{0:D2}" -f $slide.SlideIndex) 882 509 30 14 8 $script:muted $false 3 | Out-Null
    return $slide
}

function Add-Bullets($slide, [string[]]$items, $x, $y, $w, $size=18, $color=$script:white, $gap=42) {
    $i = 0
    foreach ($item in $items) {
        $cy = $y + ($i * $gap)
        $dot = $slide.Shapes.AddShape(9, $x, $cy + 7, 8, 8)
        $dot.Fill.ForeColor.RGB = $script:blue
        $dot.Line.Visible = 0
        Add-Text $slide $item ($x + 20) $cy ($w - 20) ($gap + 8) $size $color | Out-Null
        $i++
    }
}

function Add-Card($slide, $title, $body, $x, $y, $w, $h, $accent=$script:blue) {
    Add-Box $slide $x $y $w $h | Out-Null
    $stripe = $slide.Shapes.AddShape(1, $x, $y, 5, $h)
    $stripe.Fill.ForeColor.RGB = $accent
    $stripe.Line.Visible = 0
    Add-Text $slide $title ($x + 18) ($y + 15) ($w - 30) 26 16 $accent $true | Out-Null
    Add-Text $slide $body ($x + 18) ($y + 50) ($w - 30) ($h - 60) 13 $script:white | Out-Null
}

function Add-Pill($slide, $text, $x, $y, $w, $color=$script:blue) {
    $pill = Add-Box $slide $x $y $w 28 $color $true $color
    $pill.Fill.Transparency = 0.84
    Add-Text $slide $text $x ($y + 6) $w 16 10 $color $true 2 | Out-Null
}

function Add-TitleSlide($presentation) {
    $slide = $presentation.Slides.AddSlide(1, $script:coverLayout)
    Remove-LayoutPlaceholders $slide
    $slide.FollowMasterBackground = 0
    Add-Text $slide 'AUTARCH | THE GOVERNED AGENT FACTORY' 46 121 500 24 14 $script:paper $true | Out-Null
    Add-Text $slide 'Governed AI agents for finance, audit, and taxation' 46 194 690 90 36 $script:paper $true | Out-Null
    Add-Text $slide 'Every conclusion bounded, approved, and provable.' 46 294 650 30 18 $script:paper | Out-Null
    Add-Text $slide 'Enterprise pitch | September 2026' 46 361 500 28 16 $script:paper | Out-Null
}

$powerPoint = $null
$presentation = $null
try {
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $pptxPath = Join-Path $OutputDirectory 'autarch-pitch.pptx'
    $pdfPath = Join-Path $OutputDirectory 'autarch-pitch.pdf'

    if (-not (Test-Path $TemplatePath)) { throw "Template not found: $TemplatePath" }
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $powerPoint.Visible = -1
    $presentation = $powerPoint.Presentations.Add()
    $presentation.ApplyTemplate($TemplatePath)
    $script:coverLayout = $presentation.SlideMaster.CustomLayouts.Item(3)
    $script:contentLayout = $presentation.SlideMaster.CustomLayouts.Item(20)

    Add-TitleSlide $presentation

    $s = Add-Base $presentation 'Finance wants AI. It cannot compromise control.' 'THE FINANCE PROBLEM'
    Add-Text $s 'AI now reviews ledgers, tests controls, interprets tax, and prepares decisions.' 48 130 820 30 18 $script:muted | Out-Null
    Add-Bullets $s @('Broad ERP access and prompt-level guardrails','Inconsistent controls rebuilt for every finance pilot','Activity logs without workpaper-grade evidence','Approvals disconnected from deployed configuration','Material findings that still require accountable judgment') 62 184 510 17 $script:white 48
    Add-Card $s 'THE REAL BOTTLENECK' 'Financial control, evidence, and accountability at scale.' 620 205 270 150 $script:gold

    $s = Add-Base $presentation 'The category: a governed agent factory' 'THE SOLUTION'
    Add-Text $s 'One finance control plane. Many governed specialist agents.' 48 128 820 30 19 $script:gold $true | Out-Null
    Add-Card $s 'CONVENTIONAL BUILDER' "Prompts + tools`n`nModel compliance`n`nProject-specific controls`n`nLogs after the event" 55 185 360 250 $script:muted
    Add-Text $s '>' 440 278 50 50 34 $script:blue $true 2 | Out-Null
    Add-Card $s 'AUTARCH' "Authority + policy + proof`n`nRules outside the model`n`nReusable DomainPacks`n`nEvidence by construction" 500 185 390 250 $script:green

    $s = Add-Base $presentation 'From requirement to governed deployment' 'THE FACTORY PIPELINE'
    $steps = @('FINANCE NEED','FINANCE PACK','BLUEPRINT','VALIDATE + PROVE','APPROVE + RBAC','DEPLOY')
    $colors = @($script:muted,$script:blue,$script:blue,$script:gold,$script:gold,$script:green)
    for ($i=0; $i -lt $steps.Count; $i++) {
        $x = 40 + ($i * 148)
        Add-Box $s $x 205 125 72 $script:card $true $colors[$i] | Out-Null
        Add-Text $s $steps[$i] $x 228 125 18 11 $colors[$i] $true 2 | Out-Null
        if ($i -lt $steps.Count - 1) { Add-Text $s '>' ($x + 126) 224 22 24 20 $script:muted $true 2 | Out-Null }
    }
    Add-Text $s 'The factory creates declarative configuration - never arbitrary executable code.' 95 335 770 32 20 $script:white $true 2 | Out-Null
    Add-Text $s 'AI may propose the agent. The governance plane decides whether it exists.' 120 382 720 24 16 $script:gold $false 2 | Out-Null

    $s = Add-Base $presentation 'Universal governance. Finance-specific trust.' 'PLATFORM ARCHITECTURE'
    Add-Card $s 'SHARED PLATFORM' "Orchestration and memory`nCapability security and budgets`nApprovals and lifecycle`nStatic proof and signed evidence" 55 155 385 295 $script:blue
    Add-Card $s 'FINANCE DOMAINPACKS' "Accounting, audit + tax workflows`nMateriality + approval thresholds`nERP and evidence boundaries`nJurisdiction rules + evaluations" 510 155 385 295 $script:gold
    Add-Text $s 'Scale across the office of the CFO without weakening financial controls.' 100 475 760 24 17 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'The deterministic governance stack' 'CONTROL PLANE'
    $controls = @(
        @('RBAC','Who may wield authority?'), @('CAPABILITY KERNEL','What exact action?'),
        @('SCOPE + LIMITS','Where and how much?'), @('POLICY-AS-CODE','Deny or escalate?'),
        @('ECONOMIC KERNEL','Within budget and risk?'), @('APPROVAL PLANE','Who ratified it?'),
        @('SIGNED LEDGER','Can we prove it?')
    )
    for ($i=0; $i -lt $controls.Count; $i++) {
        $y = 130 + ($i * 52)
        $num = Add-Box $s 58 $y 34 34 $script:blue $true $script:blue
        Add-Text $s ($i+1).ToString() 58 ($y+8) 34 15 10 $script:navy $true 2 | Out-Null
        Add-Text $s $controls[$i][0] 112 ($y+3) 250 20 14 $script:blue $true | Out-Null
        Add-Text $s $controls[$i][1] 365 ($y+3) 460 22 14 $script:white | Out-Null
    }
    Add-Text $s 'The kernel cannot be prompted out of policy.' 580 465 310 26 16 $script:gold $true 3 | Out-Null

    $s = Add-Base $presentation 'Intelligence above. Deterministic control below.' 'REFERENCE ARCHITECTURE'
    $layers = @(
        @('FINANCE EXPERIENCES','Audit | Tax | Close | Treasury | FP&A',$script:blue),
        @('AGENT FACTORY','DomainPack | Blueprint | Registry | Approval',$script:blue),
        @('INTELLIGENCE','Planner | Council | Specialists | Evaluators',$script:muted),
        @('GOVERNANCE CHOKEPOINT','RBAC > Capability > Scope > Policy > Budget > Human',$script:gold),
        @('EXECUTION','Adapters | MCP | MAF | LangChain | SQL | Documents',$script:green),
        @('EVIDENCE','Why-memory | Signatures | Journal | Events | OTel',$script:green)
    )
    for($i=0;$i -lt $layers.Count;$i++){
        $y=126+($i*59); $edge=$layers[$i][2]
        Add-Box $s 85 $y 790 48 $(if($i -eq 3){$script:navy2}else{$script:card}) $true $edge | Out-Null
        Add-Text $s $layers[$i][0] 105 ($y+10) 230 18 12 $edge $true | Out-Null
        Add-Text $s $layers[$i][1] 340 ($y+10) 515 18 12 $script:white | Out-Null
        if($i -lt $layers.Count-1){ Add-Arrow $s 895 ($y+44) 895 ($y+61) $script:blue 2 | Out-Null }
    }
    Add-Text $s 'No planner, framework, model, or tool bypasses the chokepoint.' 105 487 750 18 13 $script:gold $true 2 | Out-Null

    $s = Add-Base $presentation 'One governed finance action: end-to-end sequence' 'RUNTIME INTERNALS'
    $runtime = @(
        @('01','INTENT','Finance request + context'), @('02','DELIBERATE','Propose + challenge'),
        @('03','NORMALIZE','Typed adapter parameters'), @('04','AUTHORIZE','Grant + scope + limits'),
        @('05','EVALUATE','Policy + budget'), @('06','PRESIDE','Rule or quorum approval'),
        @('07','EXECUTE','Trusted adapter'), @('08','ASSESS','Quality + safety panel'),
        @('09','SEAL','Sign rationale + outcome'), @('10','EMIT','Events + compliance evidence')
    )
    for($i=0;$i -lt $runtime.Count;$i++){
        $col=$i%2; $row=[math]::Floor($i/2); $x=55+($col*450); $y=130+($row*68)
        Add-Box $s $x $y 405 52 $script:card $true $(if($i -ge 3 -and $i -le 5){$script:gold}else{$script:border}) | Out-Null
        Add-Text $s $runtime[$i][0] ($x+12) ($y+14) 30 16 10 $script:gold $true | Out-Null
        Add-Text $s $runtime[$i][1] ($x+48) ($y+8) 120 16 11 $script:blue $true | Out-Null
        Add-Text $s $runtime[$i][2] ($x+48) ($y+27) 335 16 11 $script:white | Out-Null
    }
    Add-Text $s 'A failed gate prevents execution - and the refusal remains explainable.' 100 482 760 18 13 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Security architecture: authority is structural' 'ZERO-AMBIENT-AUTHORITY'
    $security = @(
        @('IDENTITY','Principal + node identity','Attributable actor/runtime'),
        @('ENTITLEMENT','RBAC-filtered grants','Role-aligned access'),
        @('LEAST PRIVILEGE','Exact/wildcard capabilities','No ambient authority'),
        @('CONFINEMENT','Scopes + quantitative limits','Bounded records/amounts'),
        @('DELEGATION','Monotonic attenuation','Child never exceeds parent'),
        @('INTEGRITY','Ed25519 + hash chain','Tamper-evident evidence')
    )
    for($i=0;$i -lt $security.Count;$i++){
        $y=128+($i*58)
        Add-Text $s $security[$i][0] 65 ($y+8) 155 18 11 $script:blue $true | Out-Null
        Add-Text $s $security[$i][1] 225 ($y+8) 300 18 12 $script:white | Out-Null
        Add-Text $s $security[$i][2] 545 ($y+8) 340 18 12 $script:green | Out-Null
        Add-Line $s 65 ($y+38) 890 ($y+38) $script:border 1 | Out-Null
    }
    Add-Text $s 'Prompt injection may alter a proposal. It cannot manufacture a grant.' 90 480 780 20 14 $script:gold $true 2 | Out-Null

    $s = Add-Base $presentation 'Memory, state, and evidence are different planes' 'DATA + ACCOUNTABILITY ARCHITECTURE'
    Add-Card $s 'WORKING CONTEXT' "Current reasoning`nCouncil + run state" 45 140 265 125 $script:blue
    Add-Card $s 'GOVERNED RECALL' "Semantic + episodic`nLong-term knowledge" 347 140 265 125 $script:blue
    Add-Card $s 'PRECEDENT' "Prior human rulings`nReusable decisions" 649 140 265 125 $script:gold
    Add-Card $s 'DURABILITY' "Resume safely`nAvoid double execution" 45 300 265 125 $script:green
    Add-Card $s 'WHY-MEMORY' "Signed rationale`nOutcome + proof" 347 300 265 125 $script:gold
    Add-Card $s 'OPERATIONS' "Events + telemetry`nMonitoring + export" 649 300 265 125 $script:green
    Add-Text $s 'Conversation history is useful context. It is not, by itself, audit evidence.' 90 475 780 20 14 $script:white $true 2 | Out-Null

    $s = Add-Base $presentation 'Portable control from finance UI to system of record' 'DEPLOYMENT TOPOLOGY'
    $nodes = @(
        @('FINANCE UI / API',75,145,210,$script:blue), @('GOVERNANCE GATEWAY',375,145,210,$script:blue),
        @('APPROVAL QUEUE',675,145,210,$script:gold), @('AUTARCH RUNTIME',375,255,210,$script:gold),
        @('ERP / TAX / TREASURY',75,375,210,$script:green), @('MODELS + MCP TOOLS',375,375,210,$script:green),
        @('SIGNED LEDGER / SIEM',675,375,210,$script:green)
    )
    foreach($n in $nodes){ Add-Box $s $n[1] $n[2] $n[3] 58 $script:card $true $n[4] | Out-Null; Add-Text $s $n[0] $n[1] ($n[2]+20) $n[3] 18 11 $n[4] $true 2 | Out-Null }
    Add-Line $s 285 174 375 174 $script:border 2 | Out-Null; Add-Line $s 585 174 675 174 $script:border 2 | Out-Null
    Add-Line $s 480 203 480 255 $script:border 2 | Out-Null
    Add-Line $s 480 313 180 375 $script:border 2 | Out-Null; Add-Line $s 480 313 480 375 $script:border 2 | Out-Null; Add-Line $s 480 313 780 375 $script:border 2 | Out-Null
    Add-Text $s 'Laptop | container | air-gapped host | distributed mesh' 110 478 740 18 14 $script:white $true 2 | Out-Null

    $s = Add-Base $presentation 'Approval is bound to what actually ships' 'IMMUTABLE GOVERNANCE'
    Add-Text $s 'SHA-256' 68 158 220 55 34 $script:gold $true | Out-Null
    Add-Text $s 'CONTENT FINGERPRINT' 70 211 250 20 11 $script:muted $true | Out-Null
    Add-Bullets $s @('Semantic versions become immutable after registration','Quorum approval binds pack + blueprint + fingerprint','Any change invalidates the prior approval','RBAC narrows authority again at compilation','Empty grants mean no authority by default') 350 145 520 16 $script:white 55
    Add-Pill $s 'NO STALE APPROVAL' 70 310 200 $script:green | Out-Null
    Add-Pill $s 'NO SILENT PRIVILEGE EXPANSION' 70 352 250 $script:green | Out-Null

    $s = Add-Base $presentation 'Prove safety before execution' 'STATIC GUARANTEES'
    Add-Card $s 'FORBID' 'file.delete can never execute' 55 155 250 125 $script:red
    Add-Card $s 'REQUIRE APPROVAL' 'payment.send never auto-executes' 355 155 250 125 $script:gold
    Add-Card $s 'CONFINE' 'file.write stays inside reports/' 655 155 250 125 $script:green
    Add-Bullets $s @('Proof applies to every attenuated child agent','Failure returns a concrete counterexample','Compilation fails closed when an invariant does not hold') 110 340 730 17 $script:white 48
    Add-Text $s 'Not a model promising good behavior - a control plane proving a boundary.' 100 475 760 24 16 $script:blue $true 2 | Out-Null

    $s = Add-Base $presentation 'Multi-agent work - without authority sprawl' 'GOVERNED ORCHESTRATION'
    Add-Box $s 350 135 260 65 $script:card $true $script:gold | Out-Null
    Add-Text $s 'SUPERVISOR' 350 157 260 20 14 $script:gold $true 2 | Out-Null
    $roles = @(@('RESEARCHER','read-only'),@('ANALYST','read + governed models'),@('REVIEWER','policy checks'),@('WRITER','scoped output'))
    for ($i=0; $i -lt 4; $i++) {
        $x = 45 + ($i * 225)
        Add-Line $s 480 200 ($x+95) 260 $script:border 2 | Out-Null
        Add-Box $s $x 260 190 105 $script:card $true $script:blue | Out-Null
        Add-Text $s $roles[$i][0] $x 281 190 20 13 $script:blue $true 2 | Out-Null
        Add-Text $s $roles[$i][1] ($x+10) 315 170 18 11 $script:white $false 2 | Out-Null
    }
    Add-Text $s 'Least privilege | isolated tools | parallel waves | unified audit chain' 80 430 800 28 18 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Factory internals: safe specialization at scale' 'AGENT FACTORY'
    $factorySteps=@('Finance requirement','Select Finance DomainPack','Resolve versioned AgentBlueprint','Validate tools + grants + policy + budget','Prove mandatory invariants','Bind quorum approval to fingerprint','Filter through principal RBAC','Compile, monitor, retire deployment')
    for($i=0;$i -lt $factorySteps.Count;$i++){
        $y=125+($i*45)
        Add-Box $s 85 $y 34 30 $(if($i -ge 4){$script:gold}else{$script:blue}) $true $(if($i -ge 4){$script:gold}else{$script:blue}) | Out-Null
        Add-Text $s ($i+1).ToString() 85 ($y+7) 34 14 9 $script:navy $true 2 | Out-Null
        Add-Text $s $factorySteps[$i] 140 ($y+5) 700 20 13 $script:white $(if($i -eq 4 -or $i -eq 5){$true}else{$false}) | Out-Null
    }
    Add-Text $s 'Scale agents without scaling unreviewed privilege.' 130 480 700 20 15 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Evidence is part of execution' 'PROVABLE OPERATIONS'
    Add-Card $s 'WHY' "Intent`nProposal + critique`nRecommendation" 55 160 245 220 $script:blue
    Add-Card $s 'CONTROL' "Kernel decision`nPolicy + budget`nHuman approval" 357 160 245 220 $script:gold
    Add-Card $s 'PROOF' "Tool outcome`nEvaluation verdict`nIdentity + signature" 659 160 245 220 $script:green
    Add-Text $s 'From agent-asserted compliance to verifiable operational evidence.' 70 430 820 30 18 $script:white $true 2 | Out-Null
    Add-Text $s 'Retention controls can redact sensitive fields while preserving chain integrity.' 110 472 740 22 13 $script:muted $false 2 | Out-Null

    $s = Add-Base $presentation 'Continuous audit and tax assurance' 'FINANCE REFERENCE SOLUTION'
    $flow = @('INGEST','RISK-RANK','TEST RULES','FIND EXCEPTIONS','REVIEW','SIGNED WORKPAPER')
    for ($i=0; $i -lt $flow.Count; $i++) {
        $x = 40 + ($i * 148)
        Add-Box $s $x 175 125 62 $script:card $true $(if($i -eq 5){$script:green}else{$script:blue}) | Out-Null
        Add-Text $s $flow[$i] $x 197 125 18 10 $(if($i -eq 5){$script:green}else{$script:blue}) $true 2 | Out-Null
        if ($i -lt 5) { Add-Text $s '>' ($x+126) 195 22 20 17 $script:muted $true 2 | Out-Null }
    }
    Add-Text $s 'Ledgers | controls | accounting treatment | tax positions | signed evidence' 65 300 830 32 17 $script:white $true 2 | Out-Null
    Add-Text $s 'The reusable pattern' 66 370 200 22 14 $script:gold $true | Out-Null
    Add-Text $s 'ingest > reason > verify > approve > act > prove' 260 368 610 26 19 $script:green $true | Out-Null
    Add-Text $s 'Finance control pattern - not autonomous audit opinion or tax advice.' 65 470 830 20 11 $script:muted $false 2 | Out-Null

    $s = Add-Base $presentation 'One platform across the office of the CFO' 'FINANCE EXPANSION'
    $markets = @(
        @('INTERNAL AUDIT','Control testing | sampling | evidence | findings'),
        @('TAXATION','Indirect tax | provisions | jurisdiction monitoring'),
        @('CONTROLLERSHIP','Journal review | reconciliations | close'),
        @('TREASURY + RISK','Cash forecasting | payment controls | exposure'),
        @('FP&A','Variance analysis | scenarios | management reporting')
    )
    for ($i=0; $i -lt $markets.Count; $i++) {
        $y = 133 + ($i * 68)
        Add-Box $s 55 $y 850 52 $script:card $true $script:border | Out-Null
        Add-Text $s $markets[$i][0] 75 ($y+15) 300 20 13 $script:blue $true | Out-Null
        Add-Text $s $markets[$i][1] 390 ($y+15) 485 20 14 $script:white | Out-Null
    }
    Add-Text $s 'Land one finance process. Reuse the controls across the CFO portfolio.' 80 480 800 22 15 $script:gold $true 2 | Out-Null

    $s = Add-Base $presentation 'Fit into the stack customers already own' 'MODEL + TOOL AGNOSTIC'
    Add-Card $s 'TOOLS' "MCP`nLangChain`nMicrosoft Agent Framework" 55 155 250 230 $script:blue
    Add-Card $s 'FINANCE DATA' "ERP + consolidation`nTax + treasury systems`nLedgers + workpapers" 355 155 250 230 $script:gold
    Add-Card $s 'MODELS' "Azure OpenAI + OpenAI`nAnthropic`nOllama / local" 655 155 250 230 $script:green
    Add-Text $s 'Autarch is the governance substrate beneath the ecosystem - not another model silo.' 75 445 810 28 17 $script:white $true 2 | Out-Null

    $s = Add-Base $presentation 'Deploy from edge to enterprise' 'PRODUCTION FOUNDATION'
    Add-Bullets $s @('Pure Python core + SQLite; zero required runtime dependencies','Offline path and first-class local-model support','Container health/readiness and structured telemetry','Durable runs, retries, rate limits, and circuit breakers','Encrypted mesh and signed distributed provenance','Typed errors for predictable integration') 60 142 550 16 $script:white 52
    Add-Box $s 650 175 220 220 $script:card $true $script:green | Out-Null
    Add-Text $s 'EDGE' 650 203 220 24 15 $script:blue $true 2 | Out-Null
    Add-Text $s 'v' 650 242 220 30 22 $script:muted $true 2 | Out-Null
    Add-Text $s 'AIR-GAPPED' 650 286 220 24 15 $script:gold $true 2 | Out-Null
    Add-Text $s 'v' 650 324 220 30 22 $script:muted $true 2 | Out-Null
    Add-Text $s 'SERVICE FLEET' 650 366 220 24 15 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Business value' 'WHY BUY'
    Add-Card $s 'SHIP FASTER' 'Reuse blueprints, controls, integrations, and approval patterns.' 55 150 385 145 $script:blue
    Add-Card $s 'REDUCE BLAST RADIUS' 'Give every agent only the tools, scope, limits, and budget it needs.' 510 150 385 145 $script:gold
    Add-Card $s 'ACCELERATE ASSURANCE' 'Generate operational evidence as part of execution - not after it.' 55 330 385 145 $script:green
    Add-Card $s 'AVOID LOCK-IN' 'Replace models and orchestration while governance remains consistent.' 510 330 385 145 $script:blue

    $s = Add-Base $presentation 'Competitive position' 'CATEGORY DIFFERENTIATION'
    $headers = @('CAPABILITY','AUTARCH','TYPICAL ORCHESTRATION-FIRST STACK')
    $widths = @(420,150,300); $xs = @(50,470,620)
    for ($i=0;$i -lt 3;$i++){ Add-Box $s $xs[$i] 132 $widths[$i] 42 $script:navy2 $false $script:border | Out-Null; Add-Text $s $headers[$i] ($xs[$i]+10) 145 ($widths[$i]-20) 16 10 $script:white $true $(if($i -eq 0){1}else{2}) | Out-Null }
    $rows = @(
        @('Versioned agent factory','NATIVE','Application-built'),
        @('Deny-by-default capability kernel','NATIVE','Tool / auth layer'),
        @('Static safety guarantees','NATIVE','Tests / policy checks'),
        @('Content-bound quorum approval','NATIVE','Workflow integration'),
        @('Signed action + evaluation evidence','NATIVE','Logging integration'),
        @('Model and tool ecosystems','INTEGRATES','Often mature / native')
    )
    for($r=0;$r -lt $rows.Count;$r++){
        $y=176+($r*48); $fill=$(if($r%2 -eq 0){$script:card}else{$script:navy2})
        for($c=0;$c -lt 3;$c++){ Add-Box $s $xs[$c] $y $widths[$c] 46 $fill $false $script:border | Out-Null; Add-Text $s $rows[$r][$c] ($xs[$c]+10) ($y+13) ($widths[$c]-20) 18 12 $(if($c -eq 1){$script:green}else{$script:white}) $(if($c -eq 1){$true}else{$false}) $(if($c -eq 0){1}else{2}) | Out-Null }
    }
    Add-Text $s 'Complement orchestration frameworks. Own the governance layer.' 90 485 780 20 14 $script:gold $true 2 | Out-Null

    $s = Add-Base $presentation 'The market has runtimes. Autarch adds a control plane.' 'COMPETITIVE LANDSCAPE'
    $landscape=@(
        @('MICROSOFT AGENT FRAMEWORK','Typed agents + graph workflows','Microsoft ecosystem, middleware, sessions'),
        @('LANGGRAPH','Low-level stateful orchestration','Durable graphs, interrupts, LangSmith'),
        @('CREWAI','Role-based crews + event-driven flows','Accessible teams, state, persistence'),
        @('OPENAI AGENTS SDK','Lightweight agents + handoffs','Minimal primitives, tools, tracing'),
        @('AUTOGEN','Event-driven multi-agent runtime','Messaging and team patterns'),
        @('AUTARCH','Governed creation + consequence','Authority, proof, signed evidence')
    )
    for($i=0;$i -lt $landscape.Count;$i++){
        $y=122+($i*59); Add-Box $s 45 $y 870 49 $script:card $true $(if($i -eq 5){$script:gold}else{$script:border}) | Out-Null
        Add-Text $s $landscape[$i][0] 60 ($y+10) 240 18 11 $(if($i -eq 5){$script:gold}else{$script:blue}) $true | Out-Null
        Add-Text $s $landscape[$i][1] 305 ($y+10) 265 18 11 $script:white | Out-Null
        Add-Text $s $landscape[$i][2] 580 ($y+10) 320 18 11 $script:muted | Out-Null
    }
    Add-Text $s 'Overlap is real. Architectural optimization is different.' 120 486 720 18 13 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Autarch vs Microsoft Agent Framework (MAF)' 'DETAILED COMPARISON'
    Add-Card $s 'MAF STRENGTHS' "Typed agents + sessions`nMiddleware + provider integrations`nGraph workflows + checkpointing`nStreaming + human request/response`nMicrosoft-supported ecosystem" 50 145 405 290 $script:blue
    Add-Card $s 'AUTARCH DIFFERENTIATION' "Scoped capability authority`nMonotonic delegation`nPre-run static safety proof`nFingerprint-bound quorum approval`nSigned rationale + outcome evidence" 505 145 405 290 $script:gold
    Add-Text $s 'Winning combination: MAF orchestrates. Autarch governs consequential functions.' 65 470 830 22 15 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Autarch vs LangGraph' 'DETAILED COMPARISON'
    Add-Card $s 'LANGGRAPH STRENGTHS' "Deterministic + agentic graph nodes`nDurable checkpoints + stores`nInterrupts + human review`nTime travel + state inspection`nLangSmith deployment + observability" 50 145 405 290 $script:blue
    Add-Card $s 'AUTARCH DIFFERENTIATION' "Kernel primitive authorization`nActor/quorum-aware approval`nUniform side-effect gating`nSafety proof before graph execution`nCryptographically attributable evidence" 505 145 405 290 $script:gold
    Add-Text $s 'Winning combination: keep LangGraph state machines; govern actions behind tool nodes.' 65 470 830 22 15 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Autarch vs CrewAI and AutoGen' 'DETAILED COMPARISON'
    Add-Card $s 'CREWS / TEAMS STRENGTHS' "Intuitive roles + delegation`nCollaborative agent patterns`nState + memory + persistence`nFlexible flow coordination`nFast multi-agent prototyping" 50 145 405 290 $script:blue
    Add-Card $s 'AUTARCH DIFFERENTIATION' "Delegation narrows real authority`nTool possession is not permission`nPolicy + economic kernels`nFleet-wide attenuation guarantees`nSigned accepted + blocked actions" 505 145 405 290 $script:gold
    Add-Text $s 'Winning combination: crews reason and collaborate; Autarch governs authority and evidence.' 65 470 830 22 15 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Autarch vs OpenAI Agents SDK' 'DETAILED COMPARISON'
    Add-Card $s 'OPENAI SDK STRENGTHS' "Productive minimal primitives`nTools, handoffs + sessions`nInput/output/tool guardrails`nHuman-in-the-loop runs`nDeep OpenAI + realtime integration" 50 145 405 290 $script:blue
    Add-Card $s 'AUTARCH DIFFERENTIATION' "Provider-neutral control contract`nGrant/scope/limit authorization`nSound unconditional-policy proof`nDurable quorum + content binding`nLocally verifiable signed evidence" 505 145 405 290 $script:gold
    Add-Text $s 'Winning combination: OpenAI agents reason and hand off; Autarch controls side effects.' 65 470 830 22 15 $script:green $true 2 | Out-Null

    $s = Add-Base $presentation 'Where Autarch supersedes - and where it complements' 'CAPABILITY MATRIX'
    $mxHeaders=@('CAPABILITY','AUTARCH','MAF','LANGGRAPH','CREWAI','OPENAI')
    $mxX=@(35,385,495,605,715,825); $mxW=@(350,110,110,110,110,100)
    for($c=0;$c -lt 6;$c++){ Add-Box $s $mxX[$c] 125 $mxW[$c] 38 $script:navy2 $false $script:border | Out-Null; Add-Text $s $mxHeaders[$c] $mxX[$c] 138 $mxW[$c] 14 9 $script:white $true 2 | Out-Null }
    $mxRows=@(
        @('Agent / workflow orchestration','YES','LEAD','LEAD','LEAD','YES'),
        @('Durable state / resume','YES','LEAD','LEAD','YES','YES'),
        @('Deny-by-default action authority','LEAD','CUSTOM','CUSTOM','CUSTOM','GUARD'),
        @('Scoped + limited delegation','LEAD','CUSTOM','CUSTOM','CUSTOM','CUSTOM'),
        @('Static safety proof','LEAD','-','-','-','-'),
        @('Content-bound quorum approval','LEAD','CUSTOM','CUSTOM','CUSTOM','CUSTOM'),
        @('Signed tamper-evident action proof','LEAD','TRACE','TRACE','TRACE','TRACE')
    )
    for($r=0;$r -lt $mxRows.Count;$r++){
        $y=165+($r*43); $fill=$(if($r%2 -eq 0){$script:card}else{$script:navy2})
        for($c=0;$c -lt 6;$c++){ Add-Box $s $mxX[$c] $y $mxW[$c] 41 $fill $false $script:border | Out-Null; Add-Text $s $mxRows[$r][$c] ($mxX[$c]+4) ($y+12) ($mxW[$c]-8) 15 $(if($c -eq 0){10}else{9}) $(if($c -eq 1){$script:green}else{$script:white}) $(if($c -eq 1){$true}else{$false}) $(if($c -eq 0){1}else{2}) | Out-Null }
    }
    Add-Text $s 'Supersedes at governance. Complements at orchestration and ecosystem.' 90 486 780 18 13 $script:gold $true 2 | Out-Null

    $s = Add-Base $presentation 'The winning architecture is compositional' 'STRATEGIC POSITION'
    Add-Box $s 110 145 740 70 $script:card $true $script:blue | Out-Null
    Add-Text $s 'MAF  |  LANGGRAPH  |  CREWAI  |  OPENAI AGENTS' 110 168 740 20 15 $script:blue $true 2 | Out-Null
    Add-Text $s 'planning | state | collaboration | developer experience' 110 193 740 16 10 $script:muted $false 2 | Out-Null
    Add-Text $s 'v' 450 222 60 26 20 $script:muted $true 2 | Out-Null
    Add-Box $s 110 258 740 85 $script:navy2 $true $script:gold | Out-Null
    Add-Text $s 'AUTARCH GOVERNANCE PLANE' 110 277 740 22 17 $script:gold $true 2 | Out-Null
    Add-Text $s 'identity | authority | policy | budget | approval | proof' 110 311 740 18 12 $script:white $false 2 | Out-Null
    Add-Text $s 'v' 450 348 60 26 20 $script:muted $true 2 | Out-Null
    Add-Box $s 110 385 740 62 $script:card $true $script:green | Out-Null
    Add-Text $s 'ERP  |  TAX  |  TREASURY  |  FILES  |  APIs  |  MCP TOOLS' 110 407 740 18 13 $script:green $true 2 | Out-Null
    Add-Text $s 'Become the layer no consequential agent should bypass.' 120 482 720 18 14 $script:white $true 2 | Out-Null

    $s = Add-Base $presentation 'Go-to-market: land with one governed workflow' 'COMMERCIAL MOTION'
    $stages = @(
        @('01','FINANCE PARTNER','Select an evidence-heavy process with a control owner.'),
        @('02','FINANCE PACK','Encode accounting, tax, authority, and evaluation.'),
        @('03','CONTROLLED PILOT','Measure coverage, reviewer hours, and false positives.'),
        @('04','CFO EXPANSION','Expand across audit, tax, close, treasury, and FP&A.')
    )
    for($i=0;$i -lt 4;$i++){
        $x=45+($i*225)
        Add-Box $s $x 150 195 265 $script:card $true $(if($i -eq 3){$script:green}else{$script:blue}) | Out-Null
        Add-Text $s $stages[$i][0] ($x+18) 170 55 32 22 $script:gold $true | Out-Null
        Add-Text $s $stages[$i][1] ($x+18) 222 160 40 13 $script:blue $true | Out-Null
        Add-Text $s $stages[$i][2] ($x+18) 280 160 100 13 $script:white | Out-Null
    }
    Add-Text $s 'Commercial wedge: governed finance workflows + reusable control packs.' 70 468 820 24 15 $script:gold $true 2 | Out-Null

    $s = Add-Base $presentation 'Readiness and responsible roadmap' 'V0.12 FRAMEWORK LINE'
    Add-Card $s 'AVAILABLE NOW' "Governed agents + orchestration`nCapabilities, policy, RBAC, budgets`nApprovals + static guarantees`nSigned provenance + evaluation`nBlueprints, DomainPacks + lifecycle" 55 145 390 315 $script:green
    Add-Card $s 'NEXT VALIDATION MILESTONES' "External security review`nDesign-partner outcomes`nSigned DomainPack distribution`nEnterprise registry service`nGoverned model-assisted proposals" 515 145 390 315 $script:gold
    Add-Text $s 'Production-oriented architecture. Production claims earned through validation.' 90 485 780 20 14 $script:white $true 2 | Out-Null

    $s = Add-Base $presentation 'Competitive research sources' 'OFFICIAL DOCUMENTATION'
    $sources=@(
        @('MICROSOFT AGENT FRAMEWORK','learn.microsoft.com/agent-framework/'),
        @('LANGGRAPH','docs.langchain.com/oss/python/langgraph/overview'),
        @('CREWAI','docs.crewai.com/'),
        @('AUTOGEN','microsoft.github.io/autogen/stable/'),
        @('OPENAI AGENTS SDK','openai.github.io/openai-agents-python/')
    )
    for($i=0;$i -lt $sources.Count;$i++){
        $y=130+($i*57)
        Add-Text $s $sources[$i][0] 70 ($y+7) 260 18 11 $script:blue $true | Out-Null
        Add-Text $s $sources[$i][1] 340 ($y+7) 550 18 11 $script:white $false 1 'Consolas' | Out-Null
        Add-Line $s 70 ($y+36) 890 ($y+36) $script:border 1 | Out-Null
    }
    Add-Box $s 70 430 820 65 $script:navy2 $true $script:gold | Out-Null
    Add-Text $s 'Reviewed 2 September 2026. Comparisons describe documented architectural emphasis; revalidate product versions, deployment requirements, and licensing during diligence.' 90 446 780 38 11 $script:muted $false 2 | Out-Null

    $s = $presentation.Slides.Add($presentation.Slides.Count + 1, 12)
    $s.FollowMasterBackground = 0
    $s.Background.Fill.Solid(); $s.Background.Fill.ForeColor.RGB = $script:navy
    Add-Pill $s 'THE ENTERPRISE AGENT CONTROL PLANE' 335 72 290 | Out-Null
    Add-Text $s 'Scale finance intelligence.' 80 150 800 62 40 $script:paper $true 2 | Out-Null
    Add-Text $s 'Preserve financial control.' 80 218 800 62 40 $script:gold $true 2 | Out-Null
    Add-Line $s 310 320 650 320 $script:blue 3 | Out-Null
    Add-Text $s 'AUTARCH - THE GOVERNED AGENT FACTORY' 150 354 660 28 18 $script:blue $true 2 | Out-Null
    Add-Text $s 'From finance requirement to approved, bounded, and provable deployment.' 120 406 720 28 17 $script:paper $false 2 | Out-Null

    $s = $presentation.Slides.Add($presentation.Slides.Count + 1, 12)
    $s.FollowMasterBackground = 0
    $s.Background.Fill.Solid(); $s.Background.Fill.ForeColor.RGB = $script:navy
    $orb = $s.Shapes.AddShape(9, 360, 80, 240, 240); $orb.Fill.ForeColor.RGB=$script:blue; $orb.Fill.Transparency=.9; $orb.Line.Visible=0
    Add-Text $s 'AUTARCH' 80 150 800 70 48 $script:paper $true 2 | Out-Null
    Add-Text $s "You don't use AI. You preside over it." 80 245 800 42 24 $script:gold $true 2 | Out-Null
    Add-Text $s 'Finance-controlled  |  Audit-ready  |  Model-agnostic  |  Provable' 100 342 760 24 15 $script:paper $false 2 | Out-Null
    Add-Text $s 'pip install autarch' 330 425 300 28 17 $script:green $true 2 'Consolas' | Out-Null

    if (Test-Path $pptxPath) { Remove-Item $pptxPath -Force }
    if (Test-Path $pdfPath) { Remove-Item $pdfPath -Force }
    $presentation.SaveAs($pptxPath, 24)
    $presentation.SaveAs($pdfPath, 32)
    $slideCount = $presentation.Slides.Count
    $presentation.Close()
    $presentation = $null
    $powerPoint.Quit()
    $powerPoint = $null
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    Write-Output "Created $slideCount slides"
    Write-Output $pptxPath
    Write-Output $pdfPath
}
finally {
    if ($presentation -ne $null) { try { $presentation.Close() } catch {} }
    if ($powerPoint -ne $null) { try { $powerPoint.Quit() } catch {} }
}
