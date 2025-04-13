 var minTemperature = 0;
        var maxTemperature = 1;
        var step = 0.05;
        var maxThermometerHeight = 200;

        // Create thermometer scale
        var thermometerScale = d3.scaleLinear()
            .domain([minTemperature, maxTemperature])
            .range([0, maxThermometerHeight]);

        // Data (temperature value)
        var temperature = 0.5; // Example temperature value (can be dynamic)

        // Create thermometer
        var svg = d3.select("#thermometer")
            .append("svg")
            .attr("class", "thermometer")
            .attr("width", 50)
            .attr("height", maxThermometerHeight);

        // Add mercury
        var mercury = svg.append("rect")
            .attr("class", "mercury")
            .attr("height", thermometerScale(temperature))
            .attr("width", "100%");

        // Function to update temperature
        function updateTemperature(newTemperature) {
            temperature = newTemperature;
            mercury.transition().duration(500).attr("height", thermometerScale(temperature));
        }

        // Example: Update temperature after 2 seconds
        setTimeout(function() {
            updateTemperature(0.75); // Change the temperature to 0.75 (example)
        }, 2000);